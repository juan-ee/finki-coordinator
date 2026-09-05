# Skill: knowledge base (Drive is the record, the cache is the index)

Use when a question needs team documents, when the knowledge cache needs a sync, or
after writing a document that belongs on Drive.

## Reading: search, then confirm

1. `knowledge_search` with plain words (it is an FTS5 MATCH — no boolean operators
   needed; malformed queries fail with an actionable message).
2. Each hit gives file_id / path / title / heading. The cache may lag Drive.
3. **Read the LIVE original on Drive via $GAPI before quoting it** — the index is a
   finding aid; Drive is current truth. If the search finds nothing, say so and offer
   a live $GAPI drive search before concluding the document does not exist.

## Syncing the cache (knowledge_sync)

1. Call `knowledge_sync` with NO arguments (plan): it returns the watermark and the
   $GAPI work order.
2. List the Drive root via $GAPI; select files whose modifiedTime is past the
   watermark; download those as text.
3. Call `knowledge_sync` again passing files=[{file_id, path, title, modified_time,
   content}] — omit content for non-text files (PDFs, binaries, Google-native exports):
   they are indexed title/path only, and their content is extracted on live read.
4. The result reports how many files were synced and the new watermark. Re-run the
   whole round after any Drive-side deletion (deleted files leave stale cache chunks
   until a full resync — deleting the cache forces one; it is rebuildable by design).

## Writing: upload after write

- Journals and digests: write locally under `journal/`, then upload the file to the
  matching Drive folder via $GAPI drive upload. If the upload fails, say so in one
  line at the end of the journal entry — drift must be visible, not silent.
- Drafts: file into `inbox/`; weekly triage uploads them into the Drive `docs/**` and
  announces what was filed.
- Never edit Drive documents in place without reading their live version first —
  Google Drive's version history is the conflict safety net, not an excuse.
