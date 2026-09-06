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
INBOX_SKILL_PATH = REPO_ROOT / "prompts" / "skills" / "inbox" / "SKILL.md"
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


def test_backup_skill_exists_with_frontmatter() -> None:
    """T2.32: the daily backup skill ships with the standard frontmatter."""
    path = REPO_ROOT / "prompts" / "skills" / "backup" / "SKILL.md"
    assert path.is_file(), "missing prompts/skills/backup/SKILL.md"
    text = path.read_text(encoding="utf-8")

    assert text.startswith("---"), "no frontmatter header"
    header = text.split("---", 2)[1]
    assert "name: coordinator-backup" in header
    assert "description: " in header
    assert "03:00" in text or "3:00" in text


def test_backup_skill_commits_before_upload() -> None:
    """Invariant 1 (T2.32): the git commit is taught BEFORE the upload, and an
    uncommitted-record upload is forbidden."""
    text = (REPO_ROOT / "prompts" / "skills" / "backup" / "SKILL.md").read_text(encoding="utf-8")

    flow_pos = text.find("## Flow")
    assert flow_pos != -1, "no Flow section"
    # The taught FLOW order is the invariant: commit is step 1, upload step 2 (the
    # intro's "commit BEFORE upload" phrasing is not the ordering evidence).
    commit_step = text.find("Commit first", flow_pos)
    upload_step = text.find("Upload the record", flow_pos)
    assert commit_step != -1, "the commit step is missing from the flow"
    assert upload_step != -1, "the upload step is missing from the flow"
    assert commit_step < upload_step, "commit must be taught before upload"
    # The job runs with data/project as the workdir: the repo-root view (data/project/docs)
    # maps to docs/ there — the taught command must be workdir-relative (review fix).
    assert "git -C docs" in text, "the workdir-relative commit command is missing"
    assert "data/project/docs" in text, "the path mapping must be stated"
    assert "never upload uncommitted" in text.lower() or "commit before upload" in text.lower()


def test_backup_skill_failure_is_loud_and_recorded() -> None:
    """Invariant 3 (T2.32): failures post loudly to the group and are recorded."""
    text = (
        (REPO_ROOT / "prompts" / "skills" / "backup" / "SKILL.md")
        .read_text(encoding="utf-8")
        .lower()
    )

    assert "loud" in text
    assert "silent" in text or "silently" in text
    assert "journal" in text


def test_backup_skill_upload_is_a_cli_file_operation() -> None:
    """Rule 11 (T2.32): the upload never pulls file content through context, and the
    reported count is verified server-side (a local count is not evidence)."""
    text = (REPO_ROOT / "prompts" / "skills" / "backup" / "SKILL.md").read_text(encoding="utf-8")

    lowered = text.lower()
    assert "never" in lowered and "context" in lowered
    assert "server-side" in lowered
    assert "$gapi" in lowered


def test_inbox_skill_exists_with_frontmatter() -> None:
    """T2.33: the Drive inbox skill ships with the standard frontmatter."""
    assert INBOX_SKILL_PATH.is_file(), "missing prompts/skills/inbox/SKILL.md"
    text = INBOX_SKILL_PATH.read_text(encoding="utf-8")

    assert text.startswith("---"), "no frontmatter header"
    header = text.split("---", 2)[1]
    assert "name: coordinator-inbox" in header
    assert "description: " in header


def test_inbox_skill_flow_is_list_download_read_write_move() -> None:
    """T2.33: the NUMBERED FLOW body teaches list -> download-to-tmp -> READ ->
    write/merge under docs/ -> move to processed/, in that order (anchored on the
    flow body, not the frontmatter description — the review finding)."""
    text = INBOX_SKILL_PATH.read_text(encoding="utf-8")

    flow_pos = text.index("## Flow")
    flow = text[flow_pos:]
    step_download = flow.index("download to tmp")
    step_read = flow.index("READ it")
    step_write = flow.index("Write or merge the new")
    step_move = flow.index("THEN move the original")
    assert step_download < step_read < step_write < step_move, (
        "the flow must teach download -> read -> write -> move, in that order"
    )
    assert "docs/" in flow[step_write:step_move], (
        "the write step must target docs/ before the move is taught"
    )


def test_inbox_skill_forbids_the_v61_gate_failure_modes() -> None:
    """T2.33: both forbidden patterns are stated — reciting content into chat as the
    'update', and verbatim file-by-file hand-copying as 'sync' (the v6.1 failure)."""
    text = INBOX_SKILL_PATH.read_text(encoding="utf-8")

    lowered = text.lower()
    assert "recite" in lowered, "the recital prohibition is missing"
    assert "hand-copy" in lowered or "hand copy" in lowered, (
        "the verbatim hand-copy prohibition is missing"
    )
    assert "chat" in lowered, "the prohibition must name the chat as the channel"
    assert "synthes" in lowered, "synthesis must be named as the job"


def test_inbox_skill_move_is_a_metadata_operation() -> None:
    """T2.33: the input/ -> processed/ move goes through $GAPI as a metadata
    operation — content never flows through context for the purpose of transfer."""
    text = INBOX_SKILL_PATH.read_text(encoding="utf-8")

    lowered = text.lower()
    assert "$gapi" in lowered
    assert "metadata" in lowered
