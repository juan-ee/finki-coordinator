---
name: coordinator-knowledge
description: Use when a question needs team documents or a document must reach the knowledge base — write/read docs/ files directly; ripgrep beats a cache.
category: productivity
---

# Skill: knowledge base (docs/ is the record — file-first, no cache)

Use when a question needs team documents, or after authoring a document that belongs in
the knowledge base. The record is the LOCAL folder `data/project/docs/`
(git-versioned): you write `.md` files there directly and read them back with your
file tools. There is NO knowledge cache and NO sync step — plain file search absorbs
all of it (v7, T2.28).

## Reading: search the files directly

1. Questions about the project → search `docs/` with your file tools first: ripgrep
   (`rg -n "phrase" docs/`) beats any cache, and the file you read IS the record —
   no confirmation step, no staleness gate, no live-read-against-a-copy.
2. Mission & goals → `docs/product/brief.md` (read before any major ask).
3. Structure → `docs/index.md` is the curated map of the folder; decisions live in
   `docs/decisions/` as numbered ADRs, meetings in `docs/meetings/`, playbooks in
   `docs/howto/`.
4. If a search finds nothing, say so — never reconstruct a document from memory.

## Writing: straight into docs/ (synthesis is the job)

- New or updated `.md` files are written directly under `docs/` — no upload step,
  no write-through. The git history is the safety net; the daily 03:00 UTC backup job
  pushes `docs/` to Drive `knowledge_base/` on its own schedule.
- Respect the existing structure (product/, decisions/, meetings/, howto/) and update
  `docs/index.md` when you add a document.
- You may READ source documents and WRITE a synthesized `.md` — transformation is
  the job. What you must NEVER do: recite a document into chat as the "knowledge base
  update", or hand-copy files verbatim file-by-file as "sync" (AGENTS.md rule 11 —
  file content never flows through your context for the purpose of transfer).

## Drive is NOT the record

- Google Drive carries exactly three folders: `input/` (humans drop documents),
  `processed/` (already ingested), `knowledge_base/` (the daily backup of `docs/`).
  Never treat a Drive copy as live truth — the local file always wins.
- Ingesting `input/` documents has its own flow: download to tmp, READ the document,
  write/merge the new `.md` under `docs/`, then move the original
  `input/` → `processed/` via $GAPI (a metadata operation — content never flows
  through your context for the purpose of transfer).
- Never edit Drive files in place as a way of updating the knowledge base: the Pi is
  the record; Drive is backup + inbox (proposal §12).
