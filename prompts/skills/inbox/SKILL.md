---
name: coordinator-inbox
description: Use when the owner asks to process the Drive inbox (or on the daily check) — list Drive input/, download to tmp, read, write the synthesized .md under docs/, then move the original to processed/.
category: productivity
---

# Skill: Drive inbox (input/ → docs/ → processed/)

Humans drop documents into the Drive `input/` folder; ingesting them is YOUR job,
on command or as part of the daily round. This is the one place where READING file
content is the job: you read the document, transform it, and write the result into
the knowledge base. The move afterwards is a metadata operation — content never
flows through your context for the purpose of TRANSFER (AGENTS.md rule 11).

## Flow (order matters)

1. **List the inbox:** `$GAPI` — list the `input/` folder on Drive. Empty inbox →
   say so in one line and stop.
2. **Per document: download to tmp.** Fetch the original to a tmp path (never work
   "from memory", never paste content into chat as a stand-in for the download).
3. **READ it.** Read the downloaded file and understand it — synthesis is the job:
   decide what the document means for the knowledge base and where it belongs
   (`docs/product/`, `docs/decisions/`, `docs/meetings/`, `docs/howto/`).
4. **Write or merge the new `.md` under `docs/`.** Write a synthesized document —
   in the knowledge base's own structure and voice, not a verbatim dump. When a
   related `.md` already exists, MERGE the new material into it (edit, don't
   duplicate). Update `docs/index.md` when you add a file. Announce what you filed
   and where, in one or two lines.
5. **THEN move the original:** `$GAPI move` the document from `input/` to
   `processed/` — a metadata operation on Drive, never a re-upload of content, and
   never BEFORE the `.md` under `docs/` exists (losing the original before the
   ingest lands loses the record).

## FORBIDDEN (the v6.1 gate failure modes — do not do these)

- **Never recite file content into chat as the "knowledge base update".** The
  update is the `.md` you write under `docs/`; the chat gets one line about what
  you filed, not the document's contents.
- **Never hand-copy files verbatim file-by-file as "sync".** Copying `input/`
  into `docs/` word-for-word is not ingestion — it skips the synthesis that makes
  the knowledge base a knowledge base. If a document genuinely needs no
  transformation, file it under `.archive/` or link it — but say so explicitly.

## Notes

- Non-text documents (PDFs, images): extract what matters on the live read
  (Hermes' document extraction is agent-side) and cite the source file's name in
  your `.md`.
- If a document is junk or a duplicate: say so in one line and move it to
  `processed/` (or `.archive/` if it should be kept out of the way) — an empty
  verdict is still a verdict.
- Failures (Drive unreachable, download errors): report plainly and stop — do not
  mark anything as ingested that is not under `docs/` yet.
