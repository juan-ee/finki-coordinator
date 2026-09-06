"""T2.30: a docs fixture builds clean through the mkdocs-material image.

Opt-in (RUN_SITE_BUILD_TEST=1): the build needs docker AND the
squidfunk/mkdocs-material image (`docker pull squidfunk/mkdocs-material`). The
default suite stays offline and fast (AGENTS.md rule 9); the Pi gate
(docs/verify/phase2.5.md) runs this on the real host.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"
IMAGE = "squidfunk/mkdocs-material:latest"

docker_missing = shutil.which("docker") is None
opted_in = os.environ.get("RUN_SITE_BUILD_TEST") == "1"
pytestmark = pytest.mark.skipif(
    docker_missing or not opted_in,
    reason="opt-in: needs docker, RUN_SITE_BUILD_TEST=1, and the mkdocs-material image",
)


def _fixture_config_from_committed() -> str:
    """Load the COMMITTED mkdocs.yml, assert its v7 shape, and retarget it at the
    fixture layout — the fixture exercises the real config, not a private copy."""
    assert MKDOCS_YML.is_file(), "the repo-root mkdocs.yml is missing"
    config = yaml.safe_load(MKDOCS_YML.read_text(encoding="utf-8"))
    assert config["docs_dir"] == "data/project/docs"
    assert config["site_dir"] == "data/site"
    assert config["theme"]["name"] == "material"
    assert ".git/" in config["exclude_docs"]
    config["docs_dir"] = "project/docs"  # fixture layout (retargeted copy below)
    config["site_dir"] = "site"
    return yaml.safe_dump(config)


def test_docs_fixture_builds_clean_without_git_or_stale_files(tmp_path: Path) -> None:
    """A fixture record (with a .git dir and a stale scratch file) builds a clean site.

    The site must contain the rendered pages and must NOT leak the record repo's
    .git internals or the excluded scratch files — the public site is content only.
    """
    (tmp_path / "mkdocs.yml").write_text(_fixture_config_from_committed(), encoding="utf-8")
    docs = tmp_path / "project" / "docs"
    (docs / "howto").mkdir(parents=True)
    (docs / "index.md").write_text(
        "# Fixture\n\nhello index — the record renders\n", encoding="utf-8"
    )
    (docs / "howto" / "x.md").write_text("# Howto\n\nbody text\n", encoding="utf-8")
    # The record repo's internals + scratch that must never reach the site:
    (docs / ".git").mkdir()
    (docs / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (docs / ".gitignore").write_text("data/\n", encoding="utf-8")
    (docs / ".DS_Store").write_text("junk\n", encoding="utf-8")

    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmp_path}:/workspace",
            IMAGE,
            "build",
            "-f",
            "/workspace/mkdocs.yml",
            "-d",
            "/workspace/site",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, f"mkdocs build failed:\n{proc.stdout}\n{proc.stderr}"
    site = tmp_path / "site"
    assert (site / "index.html").is_file(), f"site output missing:\n{sorted(site.rglob('*'))}"
    assert (site / "howto" / "x" / "index.html").is_file() or (
        site / "howto" / "x.html"
    ).is_file(), "subpage not rendered"
    leaked = [str(p) for p in site.rglob("*") if ".git" in p.parts or ".gitignore" in p.name]
    assert not leaked, f"record internals leaked into the site: {leaked}"
    assert not (site / ".DS_Store").exists(), "excluded scratch file reached the site"
