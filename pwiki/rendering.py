"""Markdown + Obsidian-flavored rendering.

Turns page text into HTML: a cached markdown-it parser plus an Obsidian
preprocessing pass for wikilinks/embeds, callouts, highlights, and `#tags`.
Wikilink/embed targets that look like a URL scheme are rejected before an href
is built. Page resolution caches normalized lookup keys on `flask.g` per request.
"""

import html
import os
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse

from flask import g

import config
from sections import _MD_FENCE_RE
from storage import get_all_pages

_markdown_parser = None
_OBSIDIAN_LINK_RE = re.compile(r'(!?)\[\[([^\]]+)\]\]')
_OBSIDIAN_HIGHLIGHT_RE = re.compile(r'==(.+?)==')
_OBSIDIAN_CALLOUT_RE = re.compile(r'^(\s*>\s*)\[!([A-Za-z][A-Za-z0-9_-]*)\]\+?\s*(.*)$')
_OBSIDIAN_TAG_RE = re.compile(r'(?<![\w/&])#([A-Za-z0-9_가-힣][A-Za-z0-9_가-힣/-]*)')


def _get_markdown_parser():
    global _markdown_parser
    if _markdown_parser is None:
        from markdown_it import MarkdownIt
        from mdit_py_plugins.front_matter import front_matter_plugin
        from mdit_py_plugins.tasklists import tasklists_plugin
        parser = (
            MarkdownIt('gfm-like', {'html': False, 'linkify': False, 'breaks': False})
            .use(front_matter_plugin)
            .use(tasklists_plugin)
        )

        default_link_open = parser.renderer.rules.get('link_open')

        def render_link_open(tokens, idx, options, env):
            token = tokens[idx]
            href = token.attrGet('href') or ''
            if _is_tag_search_href(href):
                token.attrJoin('class', 'md-tag')
            if default_link_open:
                return default_link_open(tokens, idx, options, env)
            return parser.renderer.renderToken(tokens, idx, options, env)

        def render_fence(tokens, idx, options, env):
            token = tokens[idx]
            info = (token.info or '').strip()
            lang = info.split(None, 1)[0] if info else ''
            lang_label = lang or 'text'
            code_attrs = f' class="language-{html.escape(lang, quote=True)}"' if lang else ''
            code = html.escape(token.content, quote=False)
            return (
                '<div class="code-block">'
                '<div class="code-header">'
                f'<span class="code-lang">{html.escape(lang_label)}</span>'
                '<button type="button" class="code-copy" title="Copy code">copy</button>'
                '</div>'
                f'<pre><code{code_attrs}>{code}</code></pre>'
                '</div>\n'
            )

        parser.renderer.rules['link_open'] = render_link_open
        parser.renderer.rules['fence'] = render_fence
        _markdown_parser = parser
    return _markdown_parser


def markdown_to_html(page_text: str, page_id: str = '') -> str:
    processed, highlights, images = _preprocess_obsidian_markdown(page_text)
    rendered = _get_markdown_parser().render(processed)
    for token, value in highlights:
        rendered = rendered.replace(token, f'<mark>{html.escape(value)}</mark>')
    # Sized image embeds (`![[img|640]]`) are emitted as deferred tokens during
    # preprocessing and swapped in here, after markdown-it has run, because the
    # parser runs with html=False and so cannot emit an <img> carrying width.
    for token, img_html in images:
        rendered = rendered.replace(token, img_html)
    return rendered


def _preprocess_obsidian_markdown(text: str) -> tuple[str, list[tuple[str, str]], list[tuple[str, str]]]:
    lines = []
    highlights: list[tuple[str, str]] = []
    images: list[tuple[str, str]] = []
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
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue
        processed = _replace_obsidian_callout(line)
        processed = _replace_obsidian_links(processed, images)
        processed, line_highlights = _replace_obsidian_highlights(processed, len(highlights))
        highlights.extend(line_highlights)
        processed = _replace_obsidian_tags(processed)
        lines.append(processed)
    return ''.join(lines), highlights, images


def _replace_obsidian_links(text: str, images: list[tuple[str, str]]) -> str:
    def repl(match: re.Match) -> str:
        is_embed = bool(match.group(1))
        target, label = _split_obsidian_target(match.group(2))
        if is_embed:
            return _render_obsidian_embed(target, label, images)
        return _render_obsidian_link(target, label)

    return _OBSIDIAN_LINK_RE.sub(repl, text)


_MD_SPECIAL_RE = re.compile(r'([\\*_`\[\]<>])')


def _escape_obsidian_callout_title(title: str) -> str:
    # Callout titles are rewritten into bold Markdown text. The title text comes
    # straight from the user, so we strip Markdown's own emphasis markers to
    # avoid stray formatting and the HTML-significant characters that would
    # otherwise survive `html=False` rendering.
    return _MD_SPECIAL_RE.sub(r'\\\1', title)


def _replace_obsidian_callout(line: str) -> str:
    match = _OBSIDIAN_CALLOUT_RE.match(line)
    if not match:
        return line
    prefix, kind, title = match.groups()
    label = kind.replace('-', ' ').title()
    cleaned = _escape_obsidian_callout_title(title.strip())
    suffix = f": {cleaned}" if cleaned else ''
    return f"{prefix}**{label}{suffix}**\n"


def _replace_obsidian_highlights(text: str, start_index: int) -> tuple[str, list[tuple[str, str]]]:
    highlights: list[tuple[str, str]] = []

    def repl(match: re.Match) -> str:
        token = f"@@PWIKI_MARK_{start_index + len(highlights)}@@"
        highlights.append((token, match.group(1)))
        return token

    return _OBSIDIAN_HIGHLIGHT_RE.sub(repl, text), highlights


def _replace_obsidian_tags(text: str) -> str:
    def repl(match: re.Match) -> str:
        raw = '#' + match.group(1)
        href = f"{config.URL_PREFIX}/?action=search&q={quote(raw, safe='')}"
        return f'[{raw}]({href})'

    return _OBSIDIAN_TAG_RE.sub(repl, text)


def _split_obsidian_target(raw: str) -> tuple[str, str]:
    target, label = raw, ''
    if '|' in target:
        target, label = target.split('|', 1)
    target = target.strip()
    label = label.strip() or _display_name_for_obsidian_target(target)
    return target, label


def _display_name_for_obsidian_target(target: str) -> str:
    base = target.split('#', 1)[0].split('^', 1)[0]
    return os.path.basename(base) or target


_UNSAFE_HREF_SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.\-]*:')


def _is_safe_link_target(target: str) -> bool:
    """Reject targets that look like a URL scheme (e.g. javascript:, data:, file:).
    Page paths and relative attachments fall through unchanged.
    """
    if not target:
        return False
    if _UNSAFE_HREF_SCHEME_RE.match(target):
        return False
    return True


def _is_tag_search_href(href: str) -> bool:
    """True iff the href is exactly one of the URLs produced by
    `_replace_obsidian_tags` (i.e. a tag-search link that should be pill-styled).
    """
    if not href:
        return False
    try:
        parsed = urlparse(href)
    except ValueError:
        return False
    if parsed.scheme or parsed.netloc:
        return False
    query = parse_qs(parsed.query)
    if query.get('action') != ['search']:
        return False
    q_values = query.get('q', [])
    return len(q_values) == 1 and q_values[0].startswith('#')


def _render_obsidian_link(target: str, label: str) -> str:
    if not _is_safe_link_target(target):
        return _escape_markdown_link_label(label or target)
    page, anchor = _split_link_anchor(target)
    resolved_page = _resolve_obsidian_page(page)
    href_page = resolved_page or page
    href = f"{config.URL_PREFIX}/{quote(href_page, safe='/')}{anchor}"
    exists = resolved_page is not None
    suffix = '' if exists else '?'
    return f'[{_escape_markdown_link_label(label + suffix)}]({href})'


# Obsidian image size hint: `![[img.png|640]]` (width) or `![[img.png|640x480]]`
# (width x height). Bounded to 5 digits so a stray number cannot request an
# absurd dimension; a non-matching pipe value stays a normal alt/caption.
_IMG_SIZE_RE = re.compile(r'^(\d{1,5})(?:[xX](\d{1,5}))?$')


def _parse_image_size(label: str) -> tuple[Optional[int], Optional[int]]:
    """Return (width, height) when `label` is an Obsidian image size hint, else
    (None, None). Height is None for the width-only form."""
    match = _IMG_SIZE_RE.match(label.strip())
    if not match:
        return None, None
    width = int(match.group(1))
    height = int(match.group(2)) if match.group(2) else None
    return width, height


def _register_sized_image(images: list[tuple[str, str]], src: str, alt: str,
                          width: int, height: Optional[int]) -> str:
    """Build a width/height-carrying <img> and stash it behind a deferred token.

    markdown-it runs with html=False, so an inline <img> in the source would be
    escaped; emitting a placeholder and swapping the real tag in after rendering
    (the same trick used for highlights) is how the sizing survives. All values
    are escaped/validated, so no attribute injection is possible.
    """
    token = f"@@PWIKI_IMG_{len(images)}@@"
    attrs = f' width="{width}"'
    if height is not None:
        attrs += f' height="{height}"'
    img_html = (
        f'<img src="{html.escape(src, quote=True)}" '
        f'alt="{html.escape(alt, quote=True)}"{attrs}>'
    )
    images.append((token, img_html))
    return token


def _render_obsidian_embed(target: str, label: str, images: list[tuple[str, str]]) -> str:
    if not _is_safe_link_target(target):
        return _escape_markdown_link_label(label or target)
    path = target.split('#', 1)[0].strip()
    if _is_markdown_page_ref(path):
        page, anchor = _split_link_anchor(target)
        href_page = _resolve_obsidian_page(page) or page
        href = f"{config.URL_PREFIX}/{quote(href_page, safe='/')}{anchor}"
        return f'[{_escape_markdown_link_label(label)}]({href})'

    attach_href = f"{config.URL_PREFIX}{config.ATTACH_URL}/{quote(path, safe='/')}"
    if _is_image_path(path):
        # `![[img.png|640]]` / `![[img.png|640x480]]` → sized <img>. A pipe value
        # that is not a dimension stays the image's alt text (current behavior).
        width, height = _parse_image_size(label)
        if width is not None:
            alt = _display_name_for_obsidian_target(target)
            return _register_sized_image(images, attach_href, alt, width, height)
        return f'![{_escape_markdown_link_label(label)}]({attach_href})'
    return f'[{_escape_markdown_link_label(label)}]({attach_href})'


def _split_link_anchor(target: str) -> tuple[str, str]:
    page = target
    anchor = ''
    if '#' in page:
        page, raw_anchor = page.split('#', 1)
        anchor = '#' + re.sub(r'[^A-Za-z0-9_\-가-힣]', '-', raw_anchor.strip()).strip('-')
    page = page.strip()
    if page.endswith('.md'):
        page = page[:-3]
    return page, anchor


def _resolve_obsidian_page(page: str) -> Optional[str]:
    page = page.strip()
    if not page:
        return None

    pages = get_all_pages()
    if page in pages:
        return page

    normalized_page = _normalize_obsidian_lookup_key(page)
    normalized_keys = _obsidian_normalized_keys(pages)
    full_match = normalized_keys['full'].get(normalized_page)
    if full_match:
        return full_match

    matches = normalized_keys['basename'].get(normalized_page, [])
    if len(matches) == 1:
        return matches[0]
    return None


def _obsidian_normalized_keys(pages: list[str]) -> dict[str, Any]:
    try:
        cached = getattr(g, '_normalized_keys', None)
    except RuntimeError:
        cached = None
    if cached is not None:
        return cached

    full: dict[str, str] = {}
    basename: dict[str, list[str]] = {}
    for candidate in pages:
        full.setdefault(_normalize_obsidian_lookup_key(candidate), candidate)
        basename.setdefault(_normalize_obsidian_lookup_key(os.path.basename(candidate)), []).append(candidate)
    normalized_keys = {'full': full, 'basename': basename}
    try:
        g._normalized_keys = normalized_keys
    except RuntimeError:
        pass
    return normalized_keys


def _normalize_obsidian_lookup_key(value: str) -> str:
    # NFC-normalize first so vault files written from macOS (typically NFD for
    # Hangul/CJK) match wikilinks typed elsewhere.
    value = unicodedata.normalize('NFC', value)
    value = value[:-3] if value.endswith('.md') else value
    value = value.replace('_', ' ').replace('/', ' ')
    value = re.sub(r'\s+', ' ', value.strip())
    return value.casefold()


def _is_markdown_page_ref(path: str) -> bool:
    return path.endswith('.md') or not os.path.splitext(path)[1]


def _is_image_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


def _escape_markdown_link_label(label: str) -> str:
    return label.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')


def render_page(page_text: str, page_id: str = '') -> str:
    return markdown_to_html(page_text, page_id)
