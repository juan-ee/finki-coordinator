"""Boot-config loading: schema-validate a YAML config and parse it into frozen dataclasses."""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml  # type: ignore[import-untyped]  # no stubs in lock; pyproject/uv.lock out of scope
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

_REQUIRED_KEY_RE = re.compile(r"'(.+)' is a required property")
_ADDITIONAL_KEY_RE = re.compile(r"\('(.+?)' was unexpected\)")
# Top-level sections _build_config indexes; a permissive schema must still fail clean here.
_REQUIRED_SECTIONS = ("project", "telegram", "model", "rag", "log_level")


class ConfigError(Exception):
    """Config file missing, unparsable, schema-invalid, or timezone unresolvable."""


@dataclass(frozen=True)
class ProjectConfig:
    """Identity block: project name, Shared Drive folder, and IANA timezone anchor."""

    name: str
    drive_root: str
    timezone: str


@dataclass(frozen=True)
class TelegramConfig:
    """Delivery targets: team group chat id (empty disables group broadcasts)."""

    group_id: str


@dataclass(frozen=True)
class ModelConfig:
    """LLM routing: OpenRouter provider plus the pinned default model id."""

    provider: str
    default_model: str


@dataclass(frozen=True)
class RagConfig:
    """Escalation-layer knobs: on/off switch, chunk size, and embedding model id."""

    enabled: bool
    chunk_size: int
    embed_model: str


@dataclass(frozen=True)
class Config:
    """The whole validated boot configuration, mirroring config.schema.json."""

    project: ProjectConfig
    telegram: TelegramConfig
    model: ModelConfig
    rag: RagConfig
    log_level: str


def _json_path(error: ValidationError) -> str:
    """Render a schema violation's instance path (plus missing/extra key) as a dotted JSON path."""
    parts: list[str] = [str(part) for part in error.absolute_path]
    if error.validator == "required":
        match = _REQUIRED_KEY_RE.search(error.message)
        if match:
            parts.append(match.group(1))
    elif error.validator == "additionalProperties":
        match = _ADDITIONAL_KEY_RE.search(error.message)
        if match:
            parts.append(match.group(1))
    return ".".join(parts) if parts else "$"


def _validate_against_schema(data: object, schema: dict[str, object]) -> None:
    """Raise ConfigError naming the offending JSON path for the first schema violation."""
    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(data):
        raise ConfigError(f"{_json_path(error)}: {error.message}")


def _build_config(data: object) -> Config:
    """Map a schema-validated config mapping onto the frozen dataclasses."""
    if not isinstance(data, dict):
        raise ConfigError(f"config: expected a mapping of sections; got {type(data).__name__}")
    missing = [section for section in _REQUIRED_SECTIONS if section not in data]
    if missing:
        raise ConfigError(f"config: missing required section {missing[0]!r}")
    data_map = cast(dict[str, object], data)
    project = cast(dict[str, object], data_map["project"])
    telegram = cast(dict[str, object], data_map["telegram"])
    model = cast(dict[str, object], data_map["model"])
    rag = cast(dict[str, object], data_map["rag"])
    return Config(
        project=ProjectConfig(
            name=cast(str, project["name"]),
            drive_root=cast(str, project["drive_root"]),
            timezone=cast(str, project["timezone"]),
        ),
        telegram=TelegramConfig(group_id=cast(str, telegram["group_id"])),
        model=ModelConfig(
            provider=cast(str, model["provider"]),
            default_model=cast(str, model["default_model"]),
        ),
        rag=RagConfig(
            enabled=cast(bool, rag["enabled"]),
            chunk_size=cast(int, rag["chunk_size"]),
            embed_model=cast(str, rag["embed_model"]),
        ),
        log_level=cast(str, data_map["log_level"]),
    )


def _probe_timezone(name: str) -> None:
    """Raise ConfigError naming project.timezone when the IANA key resolves to no zone."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"project.timezone: cannot resolve timezone {name!r}: {exc}") from exc


def _read_yaml(path: Path) -> object:
    """Read and parse a YAML config file, raising ConfigError for missing files or bad syntax."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config file: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc


def _read_schema(path: Path) -> dict[str, object]:
    """Read and parse the JSON schema file, raising ConfigError unless it is a JSON object."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read schema file: {exc}") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: schema file is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"{path}: schema file must contain a JSON object; got {type(parsed).__name__}"
        )
    return cast(dict[str, object], parsed)


def load_config(config_path: str | Path, schema_path: str | Path | None = None) -> Config:
    """Load a YAML config, validate it against the JSON schema, and return frozen dataclasses."""
    path = Path(config_path)
    schema_file = (
        Path(schema_path) if schema_path is not None else path.parent / "config.schema.json"
    )
    data = _read_yaml(path)
    schema = _read_schema(schema_file)
    _validate_against_schema(data, schema)
    config = _build_config(data)
    _probe_timezone(config.project.timezone)
    return config


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a config file against its schema (CLI); return a process exit code."""
    parser = argparse.ArgumentParser(
        prog="coordinator.config",
        description="Validate a coordinator YAML config against its JSON schema.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate a config file against a JSON schema")
    validate.add_argument(
        "config_path",
        nargs="?",
        default="config/config.example.yaml",
        help="config YAML path (default: config/config.example.yaml)",
    )
    validate.add_argument(
        "schema_path",
        nargs="?",
        default="config/config.schema.json",
        help="JSON schema path (default: config/config.schema.json)",
    )
    args = parser.parse_args(argv)
    try:
        load_config(args.config_path, args.schema_path)
    except ConfigError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {args.config_path} validates against {args.schema_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
