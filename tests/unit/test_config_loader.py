"""Config loader contract: the example loads into frozen dataclasses; bad configs raise ConfigError."""

import copy
import pathlib
import shutil
from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest
import yaml

from coordinator.config import (
    Config,
    ConfigError,
    ModelConfig,
    ProjectConfig,
    RagConfig,
    TelegramConfig,
    load_config,
    main,
)

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[2] / "config"
SCHEMA_PATH = CONFIG_DIR / "config.schema.json"
EXAMPLE_PATH = CONFIG_DIR / "config.example.yaml"


@pytest.fixture()
def example() -> dict:
    """Load the committed example config as a plain dict."""
    return yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))


def _write_variant(tmp_path: pathlib.Path, mutant: dict) -> pathlib.Path:
    """Serialize a mutated config dict to a YAML file under tmp_path."""
    variant_path = tmp_path / "config.yaml"
    variant_path.write_text(yaml.safe_dump(mutant), encoding="utf-8")
    return variant_path


def test_example_loads_into_frozen_dataclasses() -> None:
    """The committed example config loads into dataclasses carrying the documented values."""
    config = load_config(EXAMPLE_PATH)

    assert isinstance(config, Config)
    assert config.project == ProjectConfig(
        name="my-project", drive_root="MyProject", timezone="Europe/Berlin"
    )
    assert config.telegram == TelegramConfig(group_id="")
    assert config.model == ModelConfig(
        provider="openrouter", default_model="nousresearch/hermes-4-70b"
    )
    assert config.rag == RagConfig(enabled=False, chunk_size=800, embed_model="bge-small-en-v1.5")
    assert config.log_level == "info"


def test_config_dataclasses_are_frozen() -> None:
    """Config dataclasses reject attribute mutation."""
    config = load_config(EXAMPLE_PATH)

    with pytest.raises(FrozenInstanceError):
        config.project.name = "other"


def test_missing_file_raises_config_error(tmp_path: pathlib.Path) -> None:
    """A nonexistent config path raises ConfigError instead of leaking FileNotFoundError."""
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.yaml", SCHEMA_PATH)


def _drop_default_model(config: dict) -> None:
    """Mutation: remove the required model.default_model key."""
    del config["model"]["default_model"]


def _chunk_size_as_string(config: dict) -> None:
    """Mutation: make rag.chunk_size a string."""
    config["rag"]["chunk_size"] = "800"


def _unknown_top_level_key(config: dict) -> None:
    """Mutation: add an unknown top-level key."""
    config["budget"] = 20


def _non_iana_timezone(config: dict) -> None:
    """Mutation: set a timezone that fails the schema pattern."""
    config["project"]["timezone"] = "Not/AZone"


def _negative_chunk_size(config: dict) -> None:
    """Mutation: make rag.chunk_size negative."""
    config["rag"]["chunk_size"] = -1


EXPECTED_JSON_PATHS: dict[Callable[[dict], None], str] = {
    _drop_default_model: "model.default_model",
    _chunk_size_as_string: "rag.chunk_size",
    _unknown_top_level_key: "budget",
    _non_iana_timezone: "project.timezone",
    _negative_chunk_size: "rag.chunk_size",
}


@pytest.mark.parametrize(
    "mutate",
    [
        _drop_default_model,
        _chunk_size_as_string,
        _unknown_top_level_key,
        _non_iana_timezone,
        _negative_chunk_size,
    ],
    ids=[
        "missing-model.default_model",
        "rag.chunk_size-as-string",
        "unknown-top-level-key",
        "timezone-not-iana-looking",
        "rag.chunk_size-negative",
    ],
)
def test_invalid_variant_raises_config_error_naming_path(
    tmp_path: pathlib.Path,
    example: dict,
    mutate: Callable[[dict], None],
) -> None:
    """A single-field mutation of the example config raises ConfigError naming the JSON path."""
    mutant = copy.deepcopy(example)

    mutate(mutant)
    variant_path = _write_variant(tmp_path, mutant)

    with pytest.raises(ConfigError) as excinfo:
        load_config(variant_path, SCHEMA_PATH)

    assert EXPECTED_JSON_PATHS[mutate] in str(excinfo.value)


@pytest.mark.parametrize("tz", ["Europe/Berlin", "America/Guayaquil"])
def test_valid_iana_key_passes_zoneinfo_probe(
    tmp_path: pathlib.Path,
    example: dict,
    tz: str,
) -> None:
    """A real IANA Area/City key passes the zoneinfo probe and lands in the dataclass."""
    mutant = copy.deepcopy(example)

    mutant["project"]["timezone"] = tz
    variant_path = _write_variant(tmp_path, mutant)

    config = load_config(variant_path, SCHEMA_PATH)

    assert config.project.timezone == tz


def test_fake_iana_looking_key_fails_zoneinfo_probe(
    tmp_path: pathlib.Path,
    example: dict,
) -> None:
    """A schema-pattern-passing but unresolvable key fails the probe, naming project.timezone."""
    mutant = copy.deepcopy(example)

    mutant["project"]["timezone"] = "Xx/Yy"  # passes the schema pattern, resolves to no zone
    variant_path = _write_variant(tmp_path, mutant)

    with pytest.raises(ConfigError) as excinfo:
        load_config(variant_path, SCHEMA_PATH)

    assert "project.timezone" in str(excinfo.value)


def test_main_validate_returns_zero_for_valid_config(
    tmp_path: pathlib.Path,
    example: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() validate returns 0 and prints a one-line confirmation for a valid config."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(example), encoding="utf-8")

    exit_code = main(["validate", str(config_path), str(SCHEMA_PATH)])

    assert exit_code == 0
    assert "valid" in capsys.readouterr().out.lower()


def test_main_validate_returns_one_and_names_path_for_invalid(
    tmp_path: pathlib.Path,
    example: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() validate returns 1 and reports the failing JSON path for a schema-invalid config."""
    mutant = copy.deepcopy(example)

    mutant["rag"]["chunk_size"] = "800"
    config_path = _write_variant(tmp_path, mutant)

    exit_code = main(["validate", str(config_path), str(SCHEMA_PATH)])

    assert exit_code == 1
    assert "rag.chunk_size" in capsys.readouterr().err


def test_main_validate_returns_one_for_unparsable_yaml(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() validate returns 1 with an actionable message instead of a YAML traceback."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project: [unclosed" + chr(10), encoding="utf-8")

    exit_code = main(["validate", str(config_path), str(SCHEMA_PATH)])

    assert exit_code == 1
    assert "INVALID" in capsys.readouterr().err


def test_main_validate_defaults_are_cwd_relative(
    tmp_path: pathlib.Path,
    example: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no path args, validate uses config/config.example.yaml + config.schema.json under CWD."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.example.yaml").write_text(yaml.safe_dump(example), encoding="utf-8")
    shutil.copyfile(SCHEMA_PATH, config_dir / "config.schema.json")
    monkeypatch.chdir(tmp_path)

    exit_code = main(["validate"])

    assert exit_code == 0


# --- phase-gate red-team regressions (C, D, E) -----------------------------------------


def test_malformed_schema_json_raises_config_error(
    tmp_path: pathlib.Path,
    example: dict,
) -> None:
    """A syntactically broken schema file raises ConfigError, not a raw JSONDecodeError."""
    config_path = _write_variant(tmp_path, example)
    schema_path = tmp_path / "schema.json"
    schema_path.write_text('{"type": "object",', encoding="utf-8")

    with pytest.raises(ConfigError, match="schema"):
        load_config(config_path, schema_path)


def test_non_object_schema_json_raises_config_error(
    tmp_path: pathlib.Path,
    example: dict,
) -> None:
    """A schema file holding valid but non-object JSON (a list) raises ConfigError."""
    config_path = _write_variant(tmp_path, example)
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigError, match="schema"):
        load_config(config_path, schema_path)


def test_permissive_empty_schema_still_fails_clean(tmp_path: pathlib.Path) -> None:
    """An empty schema validates anything; the loader then fails with ConfigError, not KeyError."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("nonsense: 1\n", encoding="utf-8")
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigError, match="project"):
        load_config(config_path, schema_path)


def test_removed_knowledge_section_is_rejected(tmp_path: pathlib.Path, example: dict) -> None:
    """v7 (T2.28): the knowledge.* config keys are deleted with the sync machinery —
    a config still carrying the section fails schema validation (unknown top-level key)."""
    variant = copy.deepcopy(example)
    variant["knowledge"] = {"freshness_ttl_minutes": 10}
    path = _write_variant(tmp_path, variant)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path, SCHEMA_PATH)

    assert "knowledge" in str(excinfo.value)
