"""Config schema contract: the committed example validates; known-bad variants are rejected."""

import copy
import json
import pathlib
from collections.abc import Callable

import pytest
import yaml
from jsonschema import Draft202012Validator

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[2] / "config"
SCHEMA_PATH = CONFIG_DIR / "config.schema.json"
EXAMPLE_PATH = CONFIG_DIR / "config.example.yaml"


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    """Build a draft 2020-12 validator from the committed JSON schema."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


@pytest.fixture()
def example() -> dict:
    """Load the committed example config as a plain dict."""
    return yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))


def test_example_config_validates(validator: Draft202012Validator, example: dict) -> None:
    """The committed example config is valid against the committed schema (happy path)."""
    errors = list(validator.iter_errors(example))

    assert errors == []


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
    """Mutation: set a timezone whose segments are not valid IANA names."""
    config["project"]["timezone"] = "Not/AZone"


def _negative_chunk_size(config: dict) -> None:
    """Mutation: make rag.chunk_size negative."""
    config["rag"]["chunk_size"] = -1


def _knowledge_section_still_present(config: dict) -> None:
    """Mutation (T2.28): resurrect the deleted knowledge section — unknown top-level key."""
    config["knowledge"] = {"freshness_ttl_minutes": 10}


@pytest.mark.parametrize(
    "mutate",
    [
        _drop_default_model,
        _chunk_size_as_string,
        _unknown_top_level_key,
        _non_iana_timezone,
        _negative_chunk_size,
        _knowledge_section_still_present,
    ],
    ids=[
        "missing-model.default_model",
        "rag.chunk_size-as-string",
        "unknown-top-level-key",
        "timezone-not-iana-looking",
        "rag.chunk_size-negative",
        "knowledge-section-deleted-still-present",
    ],
)
def test_invalid_variant_rejected(
    validator: Draft202012Validator,
    example: dict,
    mutate: Callable[[dict], None],
) -> None:
    """A single-field mutation of the example config must fail schema validation."""
    mutant = copy.deepcopy(example)

    mutate(mutant)
    errors = list(validator.iter_errors(mutant))

    assert errors, f"expected schema rejection for mutation {mutate.__name__}"
