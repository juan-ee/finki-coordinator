"""Upstream plugin-discovery contract: manifest validity + single-arg register entrypoint.

hermes-agent at the pinned ref (docker/HERMES_REF -> hermes_cli/plugins.py) loads a
directory plugin only when (a) plugin.yaml parses beside an __init__.py, (b) the package
module exposes register(ctx) callable with a single positional argument, and (c) the
plugin id is listed in config.yaml plugins.enabled. (a)+(b) are pinned here so the
2026-09-01 gate blocker (discovery silently skipping src/coordinator) cannot regress;
(c) is applied by scripts/setup.sh step 6 and exercised on the Pi by docs/verify/phase1.md.

The MANIFEST_SCHEMA mirrors the upstream known-field census (_KNOWN_MANIFEST_FIELDS —
now v1+v2 at the pinned ref): every field we ship must be understood by the pinned
parser, and provides_tools must match TOOL_SPECS exactly, so hermes plugins list can
never disagree with the registry.
"""

import inspect
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

import coordinator
from coordinator.hermes_plugin import TOOL_SPECS

# The bind-mounted directory upstream scans: <HERMES_HOME>/plugins/coordinator on the Pi.
PLUGIN_DIR = Path(coordinator.__file__).parent
MANIFEST_PATH = PLUGIN_DIR / "plugin.yaml"

MANIFEST_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Hermes plugin.yaml (v1 subset understood by the pinned hermes-agent ref)",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        # name must equal the bind-mount directory name: upstream derives the
        # registry key from it and plugins.enabled matches on this id.
        "name": {"const": "coordinator"},
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "description": {"type": "string", "minLength": 1},
        # standalone is the only kind the general PluginManager loads for user plugins.
        "kind": {"const": "standalone"},
        "provides_tools": {
            # 9 tools since v6.1 (T2.20): the two-call knowledge_sync tool was removed;
            # sync is the deterministic script (scripts/sync_knowledge.py, rule 11).
            "type": "array",
            "minItems": 9,
            "maxItems": 9,
            "uniqueItems": True,
            "items": {"type": "string", "pattern": r"^[a-z][a-z_]*$"},
        },
    },
    "required": ["name", "version", "description", "kind", "provides_tools"],
}


def _load_manifest() -> dict[str, object]:
    """Parse plugin.yaml, asserting it exists beside the package __init__."""
    assert MANIFEST_PATH.is_file(), f"missing upstream discovery manifest: {MANIFEST_PATH}"
    assert (PLUGIN_DIR / "__init__.py").is_file(), "manifest must sit beside __init__.py"
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "plugin.yaml must be a YAML mapping"
    return data


class FakeCtx:
    """HermesContext fake: records register_tool calls verbatim for exact assertions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        schema: dict[str, object],
        handler: object,
        toolset: str,
    ) -> None:
        """Record one registration verbatim."""
        self.calls.append(
            {
                "name": name,
                "description": description,
                "schema": schema,
                "handler": handler,
                "toolset": toolset,
            }
        )


def test_manifest_exists_beside_init_and_parses() -> None:
    """The plugin directory ships a parseable plugin.yaml next to __init__.py."""
    manifest = _load_manifest()
    assert manifest["name"] == "coordinator"


def test_manifest_validates_against_discovery_schema() -> None:
    """Every manifest field is one the pinned upstream parser understands."""
    jsonschema.validate(_load_manifest(), MANIFEST_SCHEMA)


def test_manifest_declares_exactly_the_tool_specs() -> None:
    """provides_tools matches TOOL_SPECS exactly so plugin listing cannot drift."""
    manifest = _load_manifest()
    declared = manifest["provides_tools"]
    assert isinstance(declared, list)
    assert sorted(declared) == sorted(TOOL_SPECS)


def test_manifest_version_matches_package_version() -> None:
    """The manifest version equals coordinator.__version__ (single release version)."""
    manifest = _load_manifest()
    assert manifest["version"] == coordinator.__version__


def test_register_entrypoint_takes_a_single_positional_ctx() -> None:
    """coordinator.register exists and upstream's register_fn(ctx) call can bind."""
    entrypoint = getattr(coordinator, "register", None)
    assert callable(entrypoint), "package must expose register(ctx) for upstream discovery"
    signature = inspect.signature(entrypoint)
    signature.bind(object())  # raises TypeError if any further arg is required


def test_register_wires_runtime_store_and_registers_every_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """register(ctx) builds the runtime repo stack at the runtime layout and registers."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ctx = FakeCtx()

    registered = coordinator.register(ctx)

    assert registered == list(TOOL_SPECS)
    assert {call["name"] for call in ctx.calls} == set(TOOL_SPECS)
    db_path = tmp_path / "workspace" / "hermes" / "hermes-coord.db"
    assert db_path.is_file(), (
        "runtime store must live at <HERMES_HOME>/workspace/hermes/hermes-coord.db"
    )
    add = next(call["handler"] for call in ctx.calls if call["name"] == "member_add")
    added = json.loads(
        str(
            add({"name": "Ana", "timezone": "America/Guayaquil", "wake": "08:00", "telegram_id": 7})
        )
    )
    assert added["ok"] is True, added["summary"]
    listed = next(call["handler"] for call in ctx.calls if call["name"] == "member_list")
    roster = json.loads(str(listed({})))
    assert roster["ok"] is True
    members = roster["data"]["members"]
    assert [member["name"] for member in members] == ["Ana"]


def test_bound_tool_accepts_host_injected_dispatch_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream invokes handlers as handler(args, task_id=..., session_id=..., user_task=...).

    model_tools.handle_function_call -> registry.dispatch(name, args, task_id=...,
    session_id=..., user_task=...) -> entry.handler(args, **kwargs); a bound tool that
    accepts only (payload) raises TypeError and the tool call dies (2026-09-01 gate).
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ctx = FakeCtx()
    coordinator.register(ctx)
    member_list = next(call["handler"] for call in ctx.calls if call["name"] == "member_list")

    result = member_list({}, task_id="", session_id="sess-1", user_task=None)

    payload = json.loads(str(result))
    assert payload["ok"] is True


def test_bound_tool_returns_json_string_per_upstream_result_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """registry._normalize_handler_result accepts str (or the multimodal envelope) only.

    A dict result is rejected as tool_result_contract, so the adapter serializes the
    AGENTS.md result dict to JSON at the host boundary while handlers stay dict-based.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ctx = FakeCtx()
    coordinator.register(ctx)
    setting_get = next(call["handler"] for call in ctx.calls if call["name"] == "setting_get")

    raw = setting_get({"key": "digest_chat"}, task_id=None, session_id="", user_task=None)

    assert isinstance(raw, str), "upstream only accepts str tool results"
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["data"]["value"] == "dm"  # DEFAULTS fallback for digest_chat
