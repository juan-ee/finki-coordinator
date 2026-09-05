# Skill: knowledge base (Drive is the record, the cache is the index)

Use when a question needs team documents, or after writing a document that belongs
on Drive. You NEVER sync the cache yourself and never pass file content through
tools — the cache is refreshed by a deterministic script (hard rule 11).

## Reading: search, then confirm

1. `knowledge_search` with plain words (it is an FTS5 MATCH — no boolean operators
   needed; malformed queries fail with an actionable message).
2. Each hit gives file_id / path / title / heading. The cache may lag Drive.
3. **Read the LIVE original on Drive via $GAPI before quoting it** — the index is a
   finding aid; Drive is current truth. If the search finds nothing, say so and offer
   a live $GAPI drive search before concluding the document does not exist.

## The cache is refreshed by the script — not by you

The cache (`knowledge_search`'s index) is maintained by
`scripts/sync_knowledge.py`: it lists the knowledge Drive, diffs against the
watermark, downloads changed text files, and ingests them through the plugin's own
chunker/repository. It runs on a nightly `hermes cron` job and on demand via
`make sync` — **you have no sync tool and must never shuttle file content through
your context** (AGENTS.md hard rule 11). Non-text files (PDFs, binaries,
Google-native exports) are indexed title/path only; extract their content on a live
read via $GAPI. After a Drive-side deletion, chunks stay cached until the next
`--resync` (the cache is rebuildable by design) — live reads keep you honest.

## Writing: upload, then sync (mandatory write-through)

- Journals and digests: write locally under `journal/`, then upload the file to the
  matching Drive folder via $GAPI drive upload. If the upload fails, say so in one
  line at the end of the journal entry — drift must be visible, not silent.
- **After EVERY successful upload, make the new document findable: run the sync
  one-shot via your shell tool — `python3 /opt/data/scripts/sync_knowledge.py`**
  (the in-container equivalent of the host's `make sync`), then report the counts
  line. The write-through only works if every writer runs it — skipping it leaves
  your own upload unsearchable until the nightly job. If the sync fails, say so in
  one line — drift must be visible, not silent. The script prints counts, paths and
  the watermark only; never fetch file content into your context.
- Drafts: file into `inbox/`; weekly triage uploads them into the Drive `docs/**` and
  announces what was filed.
- Never edit Drive documents in place without reading their live version first —
  Google Drive's version history is the conflict safety net, not an excuse.
