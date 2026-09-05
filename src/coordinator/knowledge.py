"""Markdown -> FTS5 chunking for the knowledge cache (pure core, no I/O)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """One indexable section of a document: heading label (None = preamble) + body."""

    heading: str | None
    body: str


def _fence_run(line: str, char: str) -> int:
    """Return the length of line's leading run of char (0 when line does not start with it)."""
    return len(line) - len(line.lstrip(char))


def chunk_markdown(body: str) -> list[Chunk]:
    """Split markdown into one chunk per '## ' section; '###' stays inside its section.

    Content before the first '## ' becomes a single None-heading preamble chunk; a
    document without any '## ' heading is one chunk; a blank document yields no chunks;
    duplicate headings are disambiguated in document order against a used-set so the
    cache's UNIQUE(file_id, heading) constraint can hold: the first occurrence keeps
    its literal name, and a taken heading (literal or already suffixed) gets the first
    free occurrence suffix ("Notes", "Notes (2)", "Notes (3)", or "Notes (2) (2)" when
    a literal "Notes (2)" arrives after a generated one). Bodies are stripped at the edges.
    Lines are CommonMark lines only: CRLF and lone CR are normalized to LF and the
    split is on LF exclusively - U+2028/U+2029/NEL/VT/FF are mid-line content, never
    line endings, so a '## ' after one does not start a section. '## ' lines inside a
    fenced code block are body text, not headings: a fence opens on a line starting
    with three or more backticks or tildes (trailing text allowed - the info string)
    and closes only on a line starting with at least as many of the SAME character as
    the opening run, so a tilde line never closes a backtick fence and a three-backtick
    line never closes a four-backtick one; an unclosed fence suppresses splits to the
    end of the document.
    """
    if body.strip() == "":
        return []

    headings: list[str | None] = []
    regions: list[list[str]] = []
    fence_char: str | None = None  # the open fence's character: "`" or "~"
    fence_run = 0  # the open fence's opening run length
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line in lines:
        if fence_char is None:
            for char in ("`", "~"):
                run = _fence_run(line, char)
                if run >= 3:
                    fence_char = char
                    fence_run = run
                    break
        elif _fence_run(line, fence_char) >= fence_run:
            fence_char = None  # same character, at least as long: closes the fence
        if fence_char is None and line.startswith("## "):
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
