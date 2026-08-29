"""CI workflow contract: ci.yml parses and runs exactly the Makefile-driven steps in order."""

import pathlib

import yaml

WORKFLOW_PATH = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"

LINT_TYPE_TEST = "uv run make lint && uv run make type && uv run make test"
VALIDATE_EXAMPLE = (
    "uv run python -m coordinator.config validate"
    " config/config.example.yaml config/config.schema.json"
)


def test_workflow_yaml_parses() -> None:
    """ci.yml exists, parses as YAML, and matches the CI contract from ROADMAP T0.2."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    job = workflow["jobs"]["ci"]
    steps = job["steps"]
    uses = [step.get("uses", "") for step in steps]
    runs = [step["run"] for step in steps if "run" in step]

    assert runs == [
        "uv sync --frozen",
        LINT_TYPE_TEST,
        VALIDATE_EXAMPLE,
    ]
    assert "actions/checkout" in uses[0]
    assert any(use.startswith("astral-sh/setup-uv") for use in uses)
    setup_uv = next(step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv"))
    assert setup_uv["with"] == {"python-version": "3.11"}
    # PyYAML resolves the YAML 1.1 boolean-like `on:` key to True.
    triggers = workflow[True]
    assert "push" in triggers
    assert "pull_request" in triggers
