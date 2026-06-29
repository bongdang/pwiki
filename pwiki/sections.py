"""Markdown section splitting helpers.

Pure text utilities with no Flask dependency: split a Markdown document into
heading-delimited sections so the editor can load/replace a single section.
Headings inside fenced code blocks are ignored.
"""

import re

_MD_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)

_MD_FENCE_RE = re.compile(r'^[ \t]{0,3}(`{3,}|~{3,})')


def _heading_positions(text: str) -> list:
    """Return markdown heading start offsets, ignoring fenced code blocks."""
    heading_re = _MD_HEADING_RE

    positions = []
    offset = 0
    in_fence = False
    fence_char = ''
    fence_len = 0

    for line in text.splitlines(keepends=True):
        fence_match = _MD_FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            marker_char = marker[0]
            marker_len = len(marker)
            if not in_fence:
                in_fence = True
                fence_char = marker_char
                fence_len = marker_len
            elif marker_char == fence_char and marker_len >= fence_len:
                in_fence = False
                fence_char = ''
                fence_len = 0
            offset += len(line)
            continue

        if not in_fence and heading_re.match(line):
            positions.append(offset)
        offset += len(line)

    return positions


def _split_sections(text: str) -> list:
    """Split text into section parts.

    Heading syntax is markdown (`#{1,6} ...`).
    index 0: content before the first heading (preamble)
    index 1..N: from each heading through the text before the next heading
    """
    positions = _heading_positions(text)
    if not positions:
        return [text]
    parts = [text[:positions[0]]]           # preamble (may be empty)
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        parts.append(text[pos:end])
    return parts


def _section_level(part: str) -> int:
    """Return the heading level for a section part; the preamble is 0."""
    m = _MD_HEADING_RE.match(part)
    return len(m.group(1)) if m else 0


def _section_range(parts: list, n: int) -> tuple:
    """Return the [n, end) range covering a section and its child sections."""
    if n <= 0 or n >= len(parts):
        return n, n + 1
    base_level = _section_level(parts[n])
    end = n + 1
    while end < len(parts):
        if _section_level(parts[end]) <= base_level:
            break
        end += 1
    return n, end


def _get_section(text: str, n: int) -> str:
    """Return section n, including child sections."""
    parts = _split_sections(text)
    if not (0 <= n < len(parts)):
        return text
    start, end = _section_range(parts, n)
    return ''.join(parts[start:end])


def _replace_section(text: str, n: int, new_text: str) -> str:
    """Return the full text with section n, including child sections, replaced."""
    parts = _split_sections(text)
    if not (0 <= n < len(parts)):
        return text
    start, end = _section_range(parts, n)
    # If another part follows, make new_text end with \n so the next heading
    # starts at the beginning of a line.
    if end < len(parts) and new_text and not new_text.endswith('\n'):
        new_text += '\n'
    return ''.join(parts[:start] + [new_text] + parts[end:])
