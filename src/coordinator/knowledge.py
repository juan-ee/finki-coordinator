"""Markdown -> FTS5 chunking for the knowledge cache (pure core, no I/O)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """One indexable section of a document: heading label (None = preamble) + body."""

    heading: str | None
    body: str


def chunk_markdown(body: str) -> list[Chunk]:
    """Split markdown into one chunk per '## ' section; '###' stays inside its section.

    Content before the first '## ' becomes a single None-heading preamble chunk; a
    document without any '## ' heading is one chunk; a blank document yields no chunks;
    duplicate headings are disambiguated in document order against a used-set so the
    cache's UNIQUE(file_id, heading) constraint can hold: the first occurrence keeps
    its literal name, and a taken heading (literal or already suffixed) gets the first
    free occurrence suffix ("Notes", "Notes (2)", "Notes (3)", or "Notes (2) (2)" when
    a literal "Notes (2)" arrives after a generated one). Bodies are stripped at the edges.
    '## ' lines inside a ``` / ~~~ code fence are body text, not headings: a fence
    line (leading ``` or ~~~, trailing text allowed) toggles fence state, and an
    unclosed fence suppresses splits to the end of the document.
    """
    if body.strip() == "":
        return []

    headings: list[str | None] = []
    regions: list[list[str]] = []
    in_fence = False
    for line in body.splitlines():
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence  # the fence line itself stays in the body
        if not in_fence and line.startswith("## "):
            headings.append(line[3:].strip())
            regions.append([])
            continue
        if not regions:
            headings.append(None)
            regions.append([])
        regions[-1].append(line)

    chunks: list[Chunk] = []
    used: set[str] = set()
    for heading, region in zip(headings, regions):
        text = "\n".join(region).strip()
        if heading is None:
            if text == "":
                continue  # blank lines before the first heading are not a preamble
        else:
            if heading in used:  # literal or generated: first come keeps its name
                n = 2
                while f"{heading} ({n})" in used:
                    n += 1
                heading = f"{heading} ({n})"
            used.add(heading)
        chunks.append(Chunk(heading=heading, body=text))
    return chunks
