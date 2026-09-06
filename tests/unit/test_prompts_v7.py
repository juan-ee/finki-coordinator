"""Runtime prompt contract (v7, T2.29): file-first authoring + the copy-pipe rule.

The shipped prompts must teach the v7 knowledge model: knowledge-base changes are
written straight under `docs/`, read back with file tools, Google Drive is never
treated as live truth — and AGENTS.md rule 11's synthesis-vs-copy-pipe prohibitions
(never recite a document into chat as the "update"; never hand-copy files verbatim
file-by-file as "sync") are stated where the agent could be tempted into them.
"""

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PERSONA_PATH = REPO_ROOT / "prompts" / "persona.md"
KNOWLEDGE_SKILL_PATH = REPO_ROOT / "prompts" / "skills" / "knowledge" / "SKILL.md"
PROJECT_README_PATH = REPO_ROOT / "project-template" / "README.md"


def test_persona_routes_kb_changes_straight_under_docs() -> None:
    """The persona's editorial policy writes docs/ directly (v7 flips the inbox rule)."""
    text = PERSONA_PATH.read_text(encoding="utf-8")

    assert "docs/" in text
    assert "straight under" in text or "directly under" in text
    assert "knowledge_search" not in text
    assert "Drive is the record" not in text
    assert "knowledge cache" not in text.lower()


def test_persona_carries_the_copy_pipe_rule() -> None:
    """The persona states both rule-11 prohibitions: recital and verbatim hand-copying."""
    text = PERSONA_PATH.read_text(encoding="utf-8")

    assert "recite" in text, "the 'never recite a document as the update' rule is missing"
    assert "hand-copy" in text, "the 'never hand-copy verbatim as sync' rule is missing"
    assert "synthes" in text.lower(), "synthesis (transformation is the job) is not stated"


def test_persona_treats_drive_as_backup_and_inbox_only() -> None:
    """The persona never lets a Drive copy masquerade as live truth."""
    text = PERSONA_PATH.read_text(encoding="utf-8").lower()

    assert "never treat" in text or "never treated" in text
    assert "live truth" in text
    assert "input/" in text and "processed/" in text and "knowledge_base/" in text


def test_knowledge_skill_stays_file_first() -> None:
    """The knowledge skill teaches ripgrep-first reading and file-first writing."""
    text = KNOWLEDGE_SKILL_PATH.read_text(encoding="utf-8")

    assert "ripgrep" in text.lower()
    assert "docs/" in text
    assert "recite" in text and "hand-copy" in text
    assert "knowledge_search" not in text
    assert "sync_knowledge" not in text


def test_project_template_readme_describes_the_v7_workspace() -> None:
    """The seeded workspace README describes the Pi-local record, not a Drive record."""
    text = PROJECT_README_PATH.read_text(encoding="utf-8")

    assert "git" in text.lower(), "the docs git repo (safety net) is not documented"
    assert "knowledge_search" not in text
    lowered = text.lower()
    assert "backup + inbox" in lowered or "backup and inbox" in lowered
    assert "the drive copy is the team's record" not in lowered


def test_digest_skill_no_longer_teaches_per_write_upload() -> None:
    """T2.29 fix-round: the digest skill must not treat a Drive copy as the record —
    the journal stays local and rides the daily 03:00 UTC backup job (T2.32)."""
    text = (REPO_ROOT / "prompts" / "skills" / "digest" / "SKILL.md").read_text(encoding="utf-8")

    lowered = text.lower()
    assert "the drive copy is the team's record" not in lowered
    assert "$gapi drive upload" not in lowered
    assert "leave the journal local" in lowered
    assert "03:00 utc" in lowered
