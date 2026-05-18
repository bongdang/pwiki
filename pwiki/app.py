#!/usr/bin/env python3
"""
pwiki - Flask Markdown wiki for Obsidian vaults
"""

import html
import hashlib
import os
import re
import secrets
import tempfile
import time
import unicodedata
from functools import lru_cache
from typing import Any, Literal, Optional, Tuple, TypedDict
from urllib.parse import quote, urlparse, parse_qs

from flask import Flask, request, render_template, redirect, session, send_from_directory, abort, g, jsonify
from loguru import logger

from dotenv_loader import load_cwd_dotenv

load_cwd_dotenv()

import config
import db
import oauth as oauth_module
import permissions
from vault import (
    GitOperationError,
    blocking_git_state,
    commit_git,
    default_commit_message,
    git_status,
    resolve_git_context,
)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=config.SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
    # Werkzeug automatically returns 413 RequestEntityTooLarge when this is exceeded.
    MAX_CONTENT_LENGTH=config.MAX_CONTENT_LENGTH,
)
_git_status_cache = {'stamp': None, 'summary': None}
_log_file_sink_id: Optional[int] = None
_log_file_sink_path: Optional[str] = None


class TreeNode(TypedDict, total=False):
    name: str
    path: str
    type: Literal['dir', 'file']
    children: dict[str, 'TreeNode'] | list['TreeNode']
    active: bool
    open: bool


@lru_cache(maxsize=32)
def _cached_static_version(filename: str) -> str:
    try:
        return str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
    except OSError:
        return '1'


def _static_version(filename: str) -> str:
    if app.debug or app.config.get('ENV') == 'development':
        try:
            return str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
        except OSError:
            return '1'
    return _cached_static_version(filename)


@app.context_processor
def _inject_static_version():
    """Expose `static_version(filename)` -> file mtime so templates can append
    `?v=<mtime>` to static asset URLs and busts the browser cache when the
    source file changes.
    """
    return {'static_version': _static_version}


def _with_url_prefix(path: str) -> str:
    return f"{config.URL_PREFIX}{path}"


def _log_file_io(message: str, *args) -> None:
    if config.FILE_IO_LOG:
        logger.info(message, *args)


def _configure_file_logging() -> None:
    global _log_file_sink_id, _log_file_sink_path
    log_file = config.LOG_FILE.strip()
    if _log_file_sink_id is not None and log_file != _log_file_sink_path:
        logger.remove(_log_file_sink_id)
        _log_file_sink_id = None
        _log_file_sink_path = None
    if not log_file or _log_file_sink_id is not None:
        return

    log_dir = os.path.dirname(os.path.abspath(os.path.expanduser(log_file)))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    _log_file_sink_id = logger.add(
        log_file,
        rotation=config.LOG_ROTATION,
        retention=config.LOG_RETENTION,
        encoding=config.HTTP_CHARSET,
    )
    _log_file_sink_path = log_file


def _git_status_summary() -> Optional[dict]:
    if not config.GIT_ROOT:
        return None
    try:
        root = os.path.abspath(config.MARKDOWN_DIR)
        context = resolve_git_context(root)
        if os.path.realpath(os.path.abspath(config.GIT_ROOT)) != str(context.git_root):
            return None
        git_dir = os.path.join(context.git_root, '.git')
        # The index mtime does not change before `git add`, so it misses
        # unstaged edits. Including the vault tree mtime also invalidates this
        # cache after web saves or external editor changes.
        stamp = (
            root,
            str(context.git_root),
            os.path.getmtime(os.path.join(git_dir, 'HEAD')) if os.path.exists(git_dir) else 0,
            os.path.getmtime(os.path.join(git_dir, 'index')) if os.path.exists(os.path.join(git_dir, 'index')) else 0,
            _latest_tree_mtime(root),
        )
    except (OSError, FileNotFoundError, NotADirectoryError, GitOperationError):
        return None

    if _git_status_cache['stamp'] == stamp:
        return _git_status_cache['summary']

    try:
        status = git_status(root)
    except (FileNotFoundError, NotADirectoryError, GitOperationError):
        return None

    branch = status.branch or '(unknown)'
    summary = {
        'branch': branch,
        'dirty': status.dirty,
        'changes': len(status.changes),
        'label': f"{branch} · {'dirty' if status.dirty else 'clean'}",
    }
    # dict.update() changes both keys in one call, which is closer to atomic
    # than two separate assignments.
    _git_status_cache.update({'stamp': stamp, 'summary': summary})
    return summary


def _git_root_matches_config(root: str) -> bool:
    if not config.GIT_ROOT:
        return False
    try:
        context = resolve_git_context(root)
    except (FileNotFoundError, NotADirectoryError, GitOperationError):
        return False
    return os.path.realpath(os.path.abspath(config.GIT_ROOT)) == str(context.git_root)


def _blocking_git_write_state() -> Optional[str]:
    if not config.GIT_ROOT:
        return None
    root = os.path.abspath(config.MARKDOWN_DIR)
    if not _git_root_matches_config(root):
        return 'Configured Git root does not match the Markdown repository.'
    try:
        return blocking_git_state(root)
    except (FileNotFoundError, NotADirectoryError, GitOperationError) as exc:
        return str(exc)


def _auto_commit_after_save(page_id: str) -> None:
    if not config.GIT_AUTO_COMMIT:
        return
    root = os.path.abspath(config.MARKDOWN_DIR)
    if not _git_root_matches_config(root):
        session['git_notice'] = {
            'level': 'error',
            'message': 'Git auto-commit skipped: PWIKI_GIT_ROOT does not match the Markdown repository.',
        }
        return

    author_email = _current_email() or session.get('username') or None
    try:
        commit_git(
            root,
            message=default_commit_message(f'{page_id}.md'),
            author_email=author_email,
            push=True,
        )
    except GitOperationError as exc:
        logger.warning("git auto-commit/push failed for page_id={!r}: {}", page_id, exc)
        session['git_notice'] = {
            'level': 'error',
            'message': f'Git auto-commit/push failed: {exc}',
        }
        return

    session['git_notice'] = {
        'level': 'ok',
        'message': 'Git auto-commit and push completed.',
    }


def _latest_tree_mtime(root: str) -> float:
    """Return the newest file/directory mtime inside the vault.

    Hidden entries starting with `.` are skipped, so `.git/` and `.obsidian/`
    changes do not affect this value. It should not be called more than once
    per request, but callers should remember each call is an O(N) tree walk.
    """
    try:
        latest = os.path.getmtime(root)
    except OSError:
        return 0.0
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not name.startswith('.')]
        for filename in filenames:
            if filename.startswith('.'):
                continue
            try:
                latest = max(latest, os.path.getmtime(os.path.join(current_root, filename)))
            except OSError:
                continue
    return latest


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def generate_csrf_token() -> str:
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(16)
    return session['csrf_token']

def validate_csrf() -> bool:
    expected = session.get('csrf_token', '')
    submitted = request.form.get('csrf_token', '')
    # Empty-vs-empty must NOT pass — an unauthenticated POST without a session
    # token would otherwise slip through with an empty form field.
    if not expected:
        return False
    return secrets.compare_digest(submitted, expected)

app.jinja_env.globals['csrf_token'] = generate_csrf_token


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def is_edit_allowed() -> bool:
    return bool(session.get('username'))

def is_admin_allowed() -> bool:
    return bool(session.get('is_admin'))

def is_read_only() -> bool:
    return bool(config.READ_ONLY)

# ---------------------------------------------------------------------------
# File / directory helpers
# ---------------------------------------------------------------------------

_INVALID_PAGE_PART_RE = re.compile(r'[\\:*?"<>|]')

def _safe_page_parts(page_id: str) -> list:
    parts = [part for part in page_id.split('/') if part]
    if (
        not parts
        or any(part in {'.', '..'} for part in parts)
        or any(_INVALID_PAGE_PART_RE.search(part) for part in parts)
    ):
        raise ValueError(f"Invalid page id: {page_id}")
    return parts

def default_new_page_id(current_page_id: str) -> str:
    if not current_page_id or '/' not in current_page_id:
        return ''
    return current_page_id.rsplit('/', 1)[0] + '/'

def get_markdown_file(page_id: str) -> str:
    root = os.path.abspath(config.MARKDOWN_DIR)
    target = os.path.abspath(os.path.join(root, *_safe_page_parts(page_id)) + '.md')
    if os.path.commonpath([root, target]) != root:
        raise ValueError(f"Invalid page id: {page_id}")
    _log_file_io("markdown resolve page_id={!r} root={!r} target={!r}", page_id, root, target)
    return target

def get_attachment_file(rel_path: str) -> str:
    root = os.path.abspath(config.MARKDOWN_DIR)
    return _get_allowed_static_file(root, rel_path, "attachment")

def get_upload_file(rel_path: str) -> str:
    root = os.path.abspath(config.UPLOAD_DIR)
    return _get_allowed_static_file(root, rel_path, "upload")

def _get_allowed_static_file(root: str, rel_path: str, label: str) -> str:
    parts = [part for part in rel_path.split('/') if part]
    if not parts or any(part in {'.', '..'} for part in parts):
        raise ValueError(f"Invalid {label} path: {rel_path}")
    target = os.path.abspath(os.path.join(root, *parts))
    if os.path.commonpath([root, target]) != root:
        raise ValueError(f"Invalid {label} path: {rel_path}")
    ext = os.path.splitext(target)[1].lower()
    if ext not in config.ATTACH_ALLOWED_EXTENSIONS:
        raise ValueError(f"{label.capitalize()} extension is not allowed: {ext}")
    return target

def get_storage_page_file(page_id: str) -> str:
    return get_markdown_file(page_id)

def read_page(page_id: str) -> Tuple[bool, int, str]:
    page_file = get_storage_page_file(page_id)
    if not os.path.exists(page_file):
        _log_file_io("markdown read missing page_id={!r} file={!r}", page_id, page_file)
        return False, 0, ''
    ok, content = read_file(page_file)
    if not ok:
        logger.warning("markdown read failed page_id={!r} file={!r}", page_id, page_file)
        return True, 0, ''
    _log_file_io("markdown read ok page_id={!r} file={!r} bytes={}", page_id, page_file, len(content.encode(config.HTTP_CHARSET)))
    return True, int(os.path.getmtime(page_file)), content

def write_page(page_id: str, text: str) -> int:
    page_file = get_storage_page_file(page_id)
    create_dir(os.path.dirname(page_file))
    write_string_to_file(page_file, text, preserve_newlines=True)
    return int(os.path.getmtime(page_file))

def build_page_tree() -> list[TreeNode]:
    root: TreeNode = {'name': '', 'path': '', 'type': 'dir', 'children': {}}
    for page_id in sorted(get_all_pages()):
        cursor = root
        parts = [part for part in page_id.split('/') if part]
        for part in parts[:-1]:
            children = cursor['children']
            cursor = children.setdefault(
                part,
                {'name': part, 'path': '/'.join(filter(None, [cursor['path'], part])), 'type': 'dir', 'children': {}},
            )
        if parts:
            filename = parts[-1]
            cursor['children'][filename] = {
                'name': filename,
                'path': page_id,
                'type': 'file',
                'children': {},
            }
    return _sort_tree_nodes(root['children'])

def _sort_tree_nodes(children: dict[str, TreeNode]) -> list[TreeNode]:
    nodes = list(children.values())
    nodes.sort(key=lambda node: (node['type'] != 'dir', node['name'].lower()))
    for node in nodes:
        node['children'] = _sort_tree_nodes(node['children'])
    return nodes


def _filter_tree_by_permission(nodes: list[TreeNode], email: Optional[str]) -> bool:
    """Hide tree nodes the user has no read access to. Returns whether any
    descendant survived (used to drop empty parent directories).
    Mutates `nodes` in place.

    The user record and path overrides are fetched once, then reused during
    recursion to avoid N+1 DB queries.
    """
    if not email:
        nodes[:] = []
        return False
    user = db.get_user_by_email(email)
    if user is None:
        nodes[:] = []
        return False
    if user.get('is_admin'):
        return bool(nodes)
    user_paths = db.list_user_paths(email)
    return _filter_tree_recursive(nodes, user, user_paths)


def _filter_tree_recursive(nodes: list[TreeNode], user: dict, user_paths: list) -> bool:
    """Recursively filter a tree using pre-loaded user/user_paths."""
    read_level = permissions.PERMISSION_LEVELS['read']

    def can_read(path: str) -> bool:
        perm = permissions.resolve_permission_for_user(path, user, user_paths)
        return permissions.PERMISSION_LEVELS.get(perm, 0) >= read_level

    surviving = []
    for node in nodes:
        if node['type'] == 'file':
            if can_read(node['path']):
                surviving.append(node)
        else:
            if _filter_tree_recursive(node['children'], user, user_paths):
                surviving.append(node)
            elif can_read(node['path']):
                # Keep an empty directory when the user has read access to that prefix.
                surviving.append(node)
    nodes[:] = surviving
    return bool(surviving)


def decorate_tree_for_render(nodes: list[TreeNode], active_page_id: str) -> bool:
    """Mutate tree nodes in place to mark active file and open ancestors.

    Sets `active=True` on the file matching `active_page_id`, and `open=True`
    on every directory on the path from root to that file. Returns whether
    this subtree contains the active page (used internally for recursion).
    """
    subtree_has_active = False
    for node in nodes:
        if node['type'] == 'file':
            node['active'] = bool(active_page_id) and node['path'] == active_page_id
            node['open'] = False
            if node['active']:
                subtree_has_active = True
        else:
            child_has_active = decorate_tree_for_render(node['children'], active_page_id)
            node['active'] = False
            node['open'] = child_has_active
            if child_has_active:
                subtree_has_active = True
    return subtree_has_active

def read_file(file_path: str) -> Tuple[bool, str]:
    try:
        with open(file_path, 'r', encoding=config.HTTP_CHARSET, newline='') as f:
            content = f.read()
            _log_file_io("file read ok file={!r} chars={}", file_path, len(content))
            return True, content
    except Exception as exc:
        logger.warning("file read error file={!r} error={!r}", file_path, exc)
        return False, ''

def _split_text_newlines(text: str) -> list[tuple[str, str]]:
    return re.findall(r'(.*?)(\r\n|\r|\n|$)', text, flags=re.DOTALL)[:-1]

def _newline_style_for_file(file_path: str) -> list[str]:
    ok, content = read_file(file_path)
    if not ok:
        return []
    return [newline for _, newline in _split_text_newlines(content) if newline]

def _apply_newline_style(text: str, existing_newlines: list[str]) -> str:
    if not existing_newlines:
        return text

    fallback = max(set(existing_newlines), key=existing_newlines.count)
    parts = _split_text_newlines(text)
    rebuilt = []
    for index, (body, newline) in enumerate(parts):
        rebuilt.append(body)
        if newline:
            rebuilt.append(existing_newlines[index] if index < len(existing_newlines) else fallback)
    return ''.join(rebuilt)

def page_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode(config.HTTP_CHARSET)).hexdigest()

def write_string_to_file(file_path: str, content: str, preserve_newlines: bool = False) -> None:
    dir_path = os.path.dirname(file_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    if preserve_newlines and os.path.exists(file_path):
        content = _apply_newline_style(content, _newline_style_for_file(file_path))
    fd, temp_path = tempfile.mkstemp(
        prefix=f'.{os.path.basename(file_path)}.',
        suffix='.tmp',
        dir=dir_path or None,
        text=True,
    )
    fd_open = True
    try:
        if os.path.exists(file_path):
            os.chmod(temp_path, os.stat(file_path).st_mode & 0o777)
        with os.fdopen(fd, 'w', encoding=config.HTTP_CHARSET, newline='') as f:
            fd_open = False
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, file_path)
    except Exception:
        if fd_open:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise

def create_dir(dir_path: str) -> None:
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)

def quote_html(text: str) -> str:
    return html.escape(text)

def get_all_pages() -> list:
    """Cache the vault page list within the current request.

    Even if several features call this during one request (wikilink resolution,
    tree building, total_pages, etc.), os.walk runs only once. Outside a
    request context (startup, CLI), each call scans without caching.
    """
    try:
        if hasattr(g, '_pages_cache'):
            return g._pages_cache
    except RuntimeError:
        pass  # outside request context
    pages = _scan_all_pages()
    try:
        g._pages_cache = pages
    except RuntimeError:
        pass
    return pages


def _scan_all_pages() -> list:
    """Walk the vault and return Markdown page ids."""
    pages = []
    root = os.path.abspath(config.MARKDOWN_DIR)
    if not os.path.exists(root):
        logger.warning("markdown root does not exist root={!r}", root)
        return pages
    if not os.path.isdir(root):
        logger.warning("markdown root is not a directory root={!r}", root)
        return pages
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for f in files:
            if f.startswith('.') or not f.endswith('.md'):
                continue
            path = os.path.join(dirpath, f)
            rel = os.path.relpath(path, root).replace(os.sep, '/')
            pages.append(rel[:-3])
    _log_file_io("markdown scan root={!r} pages={} sample={}", root, len(pages), pages[:5])
    return pages


# ---------------------------------------------------------------------------
# Section splitting helpers
# ---------------------------------------------------------------------------

_MD_HEADING_RE   = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)


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
    processed, highlights = _preprocess_obsidian_markdown(page_text)
    rendered = _get_markdown_parser().render(processed)
    for token, value in highlights:
        rendered = rendered.replace(token, f'<mark>{html.escape(value)}</mark>')
    return rendered


def _preprocess_obsidian_markdown(text: str) -> tuple[str, list[tuple[str, str]]]:
    lines = []
    highlights: list[tuple[str, str]] = []
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
        processed = _replace_obsidian_links(processed)
        processed, line_highlights = _replace_obsidian_highlights(processed, len(highlights))
        highlights.extend(line_highlights)
        processed = _replace_obsidian_tags(processed)
        lines.append(processed)
    return ''.join(lines), highlights


def _replace_obsidian_links(text: str) -> str:
    def repl(match: re.Match) -> str:
        is_embed = bool(match.group(1))
        target, label = _split_obsidian_target(match.group(2))
        if is_embed:
            return _render_obsidian_embed(target, label)
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


def _render_obsidian_embed(target: str, label: str) -> str:
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


# ---------------------------------------------------------------------------
# Permission enforcement helpers (active only when OAuth is configured;
# in legacy password mode they pass through so existing guards apply.)
# ---------------------------------------------------------------------------

def _current_email() -> Optional[str]:
    # Prefer the canonical OAuth `email` claim. Tests (and the legacy session
    # cookie shape) sometimes populate only `username`; treat that as the email
    # since OAuth callback sets both to the same value.
    return session.get('email') or session.get('username') or None


def _can_read(page_id: str) -> bool:
    """Anonymous read is allowed when OAuth is not configured.
    With OAuth on, the page must be readable for the signed-in email.
    """
    if not oauth_module.oauth_enabled():
        return True
    email = _current_email()
    return bool(email) and permissions.has_permission(email, page_id, 'read')


def _can_write(page_id: str) -> bool:
    if is_read_only():
        return False
    if not oauth_module.oauth_enabled():
        return False  # anonymous read-only mode
    email = _current_email()
    return bool(email) and permissions.has_permission(email, page_id, 'write')


def _login_redirect(page_id: str, action: str):
    if not oauth_module.oauth_enabled():
        return _render_forbidden(
            page_id,
            'Authentication is not configured, so write operations are unavailable. Contact an administrator.',
        )
    next_path = (
        f"{config.URL_PREFIX}/{quote(page_id, safe='/')}?action={action}" if page_id
        else f"{config.URL_PREFIX}/"
    )
    # Encode the whole next_path so the ?action= part is not parsed as a
    # parameter of the login URL.
    return redirect(f"{_with_url_prefix('/auth/google/login')}?next={quote(next_path, safe='/?=&')}")


def _render_forbidden(page_id: str, message: str):
    return render_template(
        'error_403.html',
        message=message,
        **ctx(page_id, 'Forbidden'),
    ), 403


def _render_error(status_code: int, title: str, message: str, page_id: str = '', detail: str = ''):
    return render_template(
        'error.html',
        error_title=title,
        error_message=message,
        error_detail=detail,
        **ctx(page_id, title),
    ), status_code


def _enforce_read(page_id: str):
    if not oauth_module.oauth_enabled():
        return None  # anonymous read-only mode allows browsing
    if not _current_email():
        return _login_redirect(page_id, 'browse')
    if not _can_read(page_id):
        return _render_forbidden(page_id, 'You do not have permission to read this page.')
    return None


def _enforce_write(page_id: str, action: str = 'edit'):
    if is_read_only():
        return _render_forbidden(page_id, 'Write mode is disabled (read-only).')
    if not oauth_module.oauth_enabled():
        return _login_redirect(page_id, action)  # no auth backend → block
    if not _current_email():
        return _login_redirect(page_id, action)
    if not _can_write(page_id):
        return _render_forbidden(page_id, 'You do not have permission to write this page.')
    return None


# ---------------------------------------------------------------------------
# Template context helper
# ---------------------------------------------------------------------------

def ctx(page_id: str = '', title: str = '') -> dict:
    page_tree = build_page_tree()
    decorate_tree_for_render(page_tree, page_id)
    oauth_active = oauth_module.oauth_enabled()
    if oauth_active:
        _filter_tree_by_permission(page_tree, _current_email())
    git_summary = _git_status_summary()
    git_notice = session.pop('git_notice', None)
    return {
        'site_name':         config.SITE_NAME,
        'charset':           config.HTTP_CHARSET,
        'page_id':           page_id,
        'title':             title or page_id.replace('_', ' ') or config.SITE_NAME,
        'use_index':         config.USE_INDEX,
        'edit_allowed':      is_edit_allowed(),
        'admin_allowed':     is_admin_allowed(),
        'username':          session.get('username', ''),
        'is_admin':          bool(session.get('is_admin')),
        'is_logged_in':      bool(session.get('username')),
        'any_auth_required': True,
        'url_prefix':        config.URL_PREFIX,
        'storage_backend':   config.STORAGE_BACKEND,
        'markup_mode':       config.MARKUP_MODE,
        'read_only':         is_read_only(),
        'default_theme':     config.DEFAULT_THEME,
        'page_tree':         page_tree,
        'total_pages':       len(get_all_pages()),
        'oauth_enabled':     oauth_active,
        'git_status':        git_summary,
        'git_notice':        git_notice,
        'can_read':          _can_read(page_id) if page_id else True,
        'can_write':         _can_write(page_id) if page_id else False,
    }


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _relative_time(ts: float) -> str:
    if not ts:
        return ''
    diff = max(0, time.time() - ts)
    if diff < 60:
        return 'just now'
    if diff < 3600:
        minutes = int(diff // 60)
        return f'{minutes} minute{"s" if minutes != 1 else ""} ago'
    if diff < 86400:
        hours = int(diff // 3600)
        return f'{hours} hour{"s" if hours != 1 else ""} ago'
    if diff < 86400 * 7:
        days = int(diff // 86400)
        return f'{days} day{"s" if days != 1 else ""} ago'
    if diff < 86400 * 30:
        weeks = int(diff // 86400 // 7)
        return f'{weeks} week{"s" if weeks != 1 else ""} ago'
    if diff < 86400 * 365:
        months = int(diff // 86400 // 30)
        return f'{months} month{"s" if months != 1 else ""} ago'
    years = int(diff // 86400 // 365)
    return f'{years} year{"s" if years != 1 else ""} ago'


def do_browse(page_id: str):
    denied = _enforce_read(page_id)
    if denied is not None:
        return denied
    try:
        exists, page_ts, page_text = read_page(page_id)
    except ValueError:
        exists, page_ts, page_text = False, 0, ''

    if not exists:
        parts = [f'<p>Page <strong>{quote_html(page_id)}</strong> does not exist.</p>']
        if not is_read_only():
            # Use quote() for URL context and keep HTML attributes delimited
            # with double quotes. quote_html(html.escape) does not escape ',
            # so single-quoted attributes could allow HTML attribute injection.
            parts.append(
                f'<p><a href="{config.URL_PREFIX}/{quote(page_id, safe="/")}?action=edit">Create this page</a></p>'
            )
        html_content = ''.join(parts)
        return render_template('browse.html', html_content=html_content,
                               **ctx(page_id, f"Not found: {page_id}"))

    if page_ts == 0 and page_text == '':
        return _render_error(
            500,
            'Could not read this page',
            'A problem occurred while reading the Markdown file. Try again later or contact an administrator.',
            page_id,
        )

    last_edited        = time.ctime(page_ts) if page_ts else ''
    last_edited_rel    = _relative_time(page_ts)
    return render_template('browse.html',
                           html_content=render_page(page_text, page_id),
                           last_edited=last_edited,
                           last_edited_rel=last_edited_rel,
                           **ctx(page_id))


def do_edit(page_id: str):
    denied = _enforce_write(page_id, action='edit')
    if denied is not None:
        return denied

    old_text  = ''
    page_time = 0

    try:
        exists, page_time, old_text = read_page(page_id)
        if not exists:
            page_time = 0
            old_text = ''
    except ValueError:
        page_time = 0
        old_text = ''

    # Section editing: when ?section=N is present, show only that section text.
    section_str = request.args.get('section', '0')
    section_num = int(section_str) if section_str.isdigit() else 0
    edit_text   = _get_section(old_text, section_num) if section_num > 0 else old_text

    return render_template('edit.html',
                           old_text=edit_text,
                           page_time=page_time,
                           old_hash=page_content_hash(old_text) if exists else '',
                           section=section_num,
                           **ctx(page_id, f"Edit: {page_id}"))


def _render_save_conflict(page_id: str, old_text: str, new_text: str, current_hash: str, page_time: int):
    now = int(time.time())
    return render_template('conflict.html',
                           old_text=old_text,
                           new_text=new_text,
                           current_hash=current_hash,
                           saved_time=time.ctime(page_time),
                           current_time=time.ctime(now),
                           new_time=now,
                           **ctx(page_id, f"Edit Conflict: {page_id}"))


def do_save(page_id: str, text: str, old_hash: str):
    denied = _enforce_write(page_id, action='edit')
    if denied is not None:
        return denied

    if not validate_csrf():
        return _render_error(
            403,
            'Invalid submission',
            'The security token is expired or invalid. Refresh the page and try again.',
            page_id,
        )

    old_text    = ''
    page_time   = 0
    section_str = request.form.get('section', '0')
    section_num = int(section_str) if section_str.isdigit() else 0

    try:
        exists, page_time, old_text = read_page(page_id)
    except ValueError:
        return _render_error(400, 'Invalid page name', 'Invalid page id.', page_id)

    current_hash = page_content_hash(old_text) if exists else ''

    # For section edits, replace only that section and rebuild the full text.
    if section_num > 0:
        text = _replace_section(old_text, section_num, text)

    # Conflict check
    if exists and old_hash != current_hash and old_text != text:
        return _render_save_conflict(page_id, old_text, text, current_hash, page_time)

    if exists and old_text == text:
        return redirect(f"{config.URL_PREFIX}/{page_id}")

    git_blocker = _blocking_git_write_state()
    if git_blocker:
        return _render_error(
            409,
            'Cannot save because of Git state',
            'Resolve the merge, rebase, or conflict state before saving again.',
            page_id,
            f'Git state blocks this save: {git_blocker}',
        )

    try:
        latest_exists, latest_page_time, latest_text = read_page(page_id)
        latest_hash = page_content_hash(latest_text) if latest_exists else ''
        if latest_hash != current_hash:
            if latest_text == text:
                return redirect(f"{config.URL_PREFIX}/{page_id}")
            return _render_save_conflict(page_id, latest_text, text, latest_hash, latest_page_time)
        write_page(page_id, text)
    except ValueError:
        return _render_error(400, 'Invalid page name', 'Invalid page id.', page_id)
    except OSError as exc:
        logger.exception("markdown write failed page_id={!r}: {}", page_id, exc)
        return _render_error(
            500,
            'Could not save this page',
            'A filesystem write failed. Your changes were not saved.',
            page_id,
        )

    _auto_commit_after_save(page_id)
    return redirect(f"{config.URL_PREFIX}/{page_id}")


def do_delete(page_id: str):
    denied = _enforce_write(page_id, action='delete')
    if denied is not None:
        return denied
    if not is_admin_allowed():
        return _render_forbidden(page_id, 'Only admins can delete pages.')

    if request.method == 'POST':
        if not validate_csrf():
            return _render_error(
                403,
                'Invalid submission',
                'The security token is expired or invalid. Refresh the page and try again.',
                page_id,
            )

        try:
            page_file = get_storage_page_file(page_id)
            if os.path.exists(page_file):
                os.remove(page_file)
        except ValueError:
            return _render_error(400, 'Invalid page name', 'Invalid page id.', page_id)
        except OSError as exc:
            logger.exception("markdown delete failed page_id={!r}: {}", page_id, exc)
            return _render_error(
                500,
                'Could not delete this page',
                'A filesystem delete failed. The file was left unchanged.',
                page_id,
            )

        return redirect(f"{config.URL_PREFIX}/")

    return render_template('delete.html', **ctx(page_id, f"Delete: {page_id}"))


def do_add_page(current_page_id: str = ''):
    denied = _enforce_write(current_page_id, action='addpage')
    if denied is not None:
        return denied
    page_name = request.args.get('page', default_new_page_id(current_page_id))
    return render_template('addpage.html',
                           page_name=page_name,
                           **ctx(current_page_id, 'Add Page'))


def do_search():
    query   = request.args.get('q', '').strip()
    results = []

    if query:
        query_lower = query.lower()
        candidates = sorted(get_all_pages())
        if oauth_module.oauth_enabled():
            candidates = permissions.filter_visible_paths(_current_email(), candidates)
        for page_id in candidates:
            exists, _, text = read_page(page_id)
            if not exists:
                continue
            title_match = query_lower in page_id.lower()
            text_match  = query_lower in text.lower()
            if title_match or text_match:
                snippet = ''
                idx = text.lower().find(query_lower)
                if idx >= 0:
                    start   = max(0, idx - 60)
                    end     = min(len(text), idx + len(query) + 60)
                    snippet = (
                        ('…' if start > 0 else '')
                        + quote_html(text[start:end])
                        + ('…' if end < len(text) else '')
                    )
                results.append({'page': page_id, 'snippet': snippet,
                                'title_match': title_match})

    return render_template('search.html',
                           query=query, results=results,
                           **ctx('', f"Search: {query}"))


def do_preview(page_id: str):
    denied = _enforce_write(page_id, action='edit')
    if denied is not None:
        return denied
    if request.method == 'POST' and not validate_csrf():
        return ("The preview security token is expired. Refresh the page and try again.", 403)
    text = request.form.get('text', '')
    return render_page(text, page_id)


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

def _check_attachment_read(filename: str):
    if not oauth_module.oauth_enabled():
        return None
    email = _current_email()
    if not email:
        abort(403)
    raw_parent = os.path.dirname(filename or '').replace('\\', '/')
    # Collapse '..' segments before ACL evaluation. Reject anything escaping
    # the vault root.
    normalized = os.path.normpath('/' + raw_parent).lstrip('/')
    if normalized.startswith('..') or normalized == '..':
        abort(403)
    if not permissions.has_permission(email, normalized, 'read'):
        abort(403)
    return None


@app.route('/upload_files/<path:filename>')
def serve_upload(filename: str):
    _check_attachment_read(filename)
    try:
        upload = get_upload_file(filename)
    except ValueError:
        abort(404)
    if not os.path.exists(upload) or not os.path.isfile(upload):
        abort(404)
    basename = os.path.basename(upload)
    return send_from_directory(os.path.dirname(upload), basename)


@app.route('/attach/<path:filename>')
def serve_attachment(filename: str):
    _check_attachment_read(filename)
    try:
        attachment = get_attachment_file(filename)
    except ValueError:
        abort(404)
    if not os.path.exists(attachment) or not os.path.isfile(attachment):
        abort(404)
    basename = os.path.basename(attachment)
    return send_from_directory(os.path.dirname(attachment), basename)


@app.route('/healthz')
def healthz():
    markdown_ok = os.path.isdir(config.MARKDOWN_DIR)
    return jsonify({
        'status': 'ok' if markdown_ok else 'degraded',
        'markdown_dir': markdown_ok,
        'read_only': config.READ_ONLY,
        'oauth_enabled': oauth_module.oauth_enabled(),
    }), 200 if markdown_ok else 503


def _resolve_landing_for(email: Optional[str]) -> Optional[str]:
    """Pick a sensible landing page for an authenticated user who has no read
    access to `config.HOME_PAGE`. Walks the permission-filtered page tree DFS,
    preferring an `index` file at the deepest folder where the user actually
    has access (matches the "index.md in my accessible folder" intent); falls back to the first
    readable file. Returns None when nothing is accessible.
    """
    if not email or not oauth_module.oauth_enabled():
        return None
    page_tree = build_page_tree()
    _filter_tree_by_permission(page_tree, email)
    if not page_tree:
        return None

    def find_in(nodes: list, parent_path: str) -> Optional[str]:
        expected_index = f"{parent_path}/index" if parent_path else 'index'
        for node in nodes:
            if node['type'] == 'file' and node['path'] == expected_index:
                return expected_index
        for node in nodes:
            if node['type'] == 'dir':
                picked = find_in(node['children'], node['path'])
                if picked:
                    return picked
        for node in nodes:
            if node['type'] == 'file':
                return node['path']
        return None

    return find_in(page_tree, '')


@app.route('/', methods=['GET', 'POST'])
@app.route('/<path:page_id>', methods=['GET', 'POST'])
def index(page_id: str = None):
    used_default_home = (page_id is None and 'id' not in request.args)
    if not page_id:
        page_id = request.args.get('id', config.HOME_PAGE)

    action = request.args.get('action', request.form.get('action', 'browse'))

    # Smart landing fallback: signed-in user with no read on HOME_PAGE gets
    # redirected to <first accessible folder>/index (if exists) or the first
    # readable page, instead of a 403 on every login.
    if (used_default_home and action == 'browse'
            and oauth_module.oauth_enabled()
            and _current_email()
            and not _can_read(page_id)):
        target = _resolve_landing_for(_current_email())
        if target:
            return redirect(f"{config.URL_PREFIX}/{quote(target, safe='/')}")
        return _render_forbidden(
            page_id,
            'You do not have access to any pages. Ask an administrator to grant access.',
        )

    handlers = {
        'browse':  lambda: do_browse(page_id),
        'edit':    lambda: do_edit(page_id),
        'save':    lambda: do_save(page_id,
                                   request.form.get('text', ''),
                                   request.form.get('oldhash', '')),
        'preview': lambda: do_preview(page_id),
        'delete':  lambda: do_delete(page_id),
        'addpage': lambda: do_add_page(page_id),
        'search':  do_search,
    }

    handler = handlers.get(action)
    if handler:
        return handler()
    return _render_error(400, 'Unknown Action', f"Unknown action: {action}", page_id)


def _register_url_prefix_routes() -> None:
    if not config.URL_PREFIX:
        return

    def add_prefixed_rule(path: str, endpoint: str, view_func, **options) -> None:
        if endpoint in app.view_functions:
            return
        app.add_url_rule(path, endpoint=endpoint, view_func=view_func, **options)

    add_prefixed_rule(
        _with_url_prefix('/static/<path:filename>'),
        'prefixed_static',
        lambda filename: send_from_directory(app.static_folder, filename),
    )
    add_prefixed_rule(
        _with_url_prefix('/upload_files/<path:filename>'),
        'prefixed_upload_files',
        serve_upload,
    )
    add_prefixed_rule(
        _with_url_prefix('/attach/<path:filename>'),
        'prefixed_attach',
        serve_attachment,
    )
    add_prefixed_rule(
        _with_url_prefix('/healthz'),
        'prefixed_healthz',
        healthz,
    )
    add_prefixed_rule(
        config.URL_PREFIX,
        'prefixed_index_base',
        index,
        methods=['GET', 'POST'],
    )
    add_prefixed_rule(
        _with_url_prefix('/'),
        'prefixed_index_root',
        index,
        methods=['GET', 'POST'],
    )
    add_prefixed_rule(
        _with_url_prefix('/<path:page_id>'),
        'prefixed_index_page',
        index,
        methods=['GET', 'POST'],
    )


_register_url_prefix_routes()


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

@app.after_request
def _set_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return response


# ---------------------------------------------------------------------------
# OAuth (Google)
# ---------------------------------------------------------------------------

def _safe_oauth_next(value: Optional[str]) -> str:
    """Accept only same-origin path-only redirect targets.
    Rejects scheme URLs (http:, javascript:, data:), protocol-relative paths
    (`//evil`), backslash escapes (`/\\evil`), and CRLF injection.
    """
    default = _with_url_prefix('/') or '/'
    if not value:
        return default
    if not value.startswith('/'):
        return default
    val_lower = value.lower()
    if val_lower.startswith('//') or val_lower.startswith('/\\') or val_lower.startswith('/%2f') or val_lower.startswith('/%5c'):
        return default
    # Forbid scheme-style colons in the path component (data:, javascript: etc.)
    if ':' in value.split('?', 1)[0].split('#', 1)[0]:
        return default
    if '\r' in value or '\n' in value:
        return default
    return value


def _build_oauth_redirect_uri() -> str:
    path = _with_url_prefix('/auth/google/callback')
    if config.PUBLIC_BASE_URL:
        return config.PUBLIC_BASE_URL + path
    return request.host_url.rstrip('/') + path


def _oauth_login():
    if not oauth_module.oauth_enabled():
        return "Google OAuth is not configured.", 503
    next_url = _safe_oauth_next(request.args.get('next', ''))
    session['oauth_next'] = next_url
    redirect_uri = _build_oauth_redirect_uri()
    return oauth_module.get_oauth().google.authorize_redirect(redirect_uri)


def _oauth_callback():
    if not oauth_module.oauth_enabled():
        abort(404)
    try:
        token = oauth_module.get_oauth().google.authorize_access_token()
        info = oauth_module.parse_userinfo(token)
    except Exception as exc:
        logger.warning("oauth callback failed: {}", exc)
        return "OAuth authentication failed.", 400

    user = db.get_user_by_sub(info['sub'])
    if user is None:
        user = db.get_user_by_email(info['email'])
    if user is None:
        logger.info("oauth login rejected (not authorized) email={!r}", info['email'])
        return _render_forbidden('', 'You do not have access. Ask an administrator to grant access.')

    db.update_login(user['email'], info['sub'], info['name'])
    session['username'] = user['email']
    session['email']    = user['email']
    session['is_admin'] = bool(user['is_admin'])
    session.pop('csrf_token', None)
    next_url = _safe_oauth_next(session.pop('oauth_next', ''))
    logger.info("oauth login ok email={!r} admin={}", user['email'], bool(user['is_admin']))
    return redirect(next_url)


def _oauth_logout():
    session.pop('username', None)
    session.pop('is_admin', None)
    session.pop('email', None)
    session.pop('oauth_next', None)
    referrer = request.referrer or ''
    # Strip scheme+host so a spoofed Referer cannot bounce us cross-origin.
    if referrer:
        try:
            parsed = urlparse(referrer)
            referrer = parsed.path + (('?' + parsed.query) if parsed.query else '')
        except ValueError:
            referrer = ''
    next_url = _safe_oauth_next(referrer) if referrer else (_with_url_prefix('/') or '/')
    return redirect(next_url)


def _init_oauth() -> None:
    """Idempotent: register OAuth client + routes when enabled."""
    oauth_module.init_oauth(app)
    if not oauth_module.oauth_enabled():
        return
    if 'oauth_google_callback' in app.view_functions:
        return
    routes = [
        ('/auth/google/login',    'oauth_google_login',    _oauth_login,    ['GET']),
        ('/auth/google/callback', 'oauth_google_callback', _oauth_callback, ['GET']),
        ('/auth/logout',          'oauth_logout',          _oauth_logout,   ['GET', 'POST']),
    ]
    for path, endpoint, fn, methods in routes:
        app.add_url_rule(path, endpoint=endpoint, view_func=fn, methods=methods)
        if config.URL_PREFIX:
            prefixed_endpoint = 'prefixed_' + endpoint
            if prefixed_endpoint not in app.view_functions:
                app.add_url_rule(
                    _with_url_prefix(path),
                    endpoint=prefixed_endpoint,
                    view_func=fn,
                    methods=methods,
                )


_init_oauth()


# ---------------------------------------------------------------------------
# Admin UI (SQLite-backed users + path ACL)
# ---------------------------------------------------------------------------

_ADMIN_PERMISSIONS = ('none', 'read', 'write')


def _require_admin_ui():
    if is_admin_allowed():
        return None
    if oauth_module.oauth_enabled():
        # Authenticated non-admin or anonymous → kick them to the OAuth flow
        # so the next session may have admin rights.
        return _login_redirect('', 'users')
    return _render_error(
        503,
        'Admin UI Is Unavailable',
        'Admin panel requires OAuth authentication, which is not configured.',
    )


def _admin_users():
    denied = _require_admin_ui()
    if denied is not None:
        return denied
    error = ''
    if request.method == 'POST':
        if not validate_csrf():
            return _render_error(
                403,
                'Invalid submission',
                'Invalid submission (CSRF check failed).',
            )
        op = request.form.get('op', 'grant')
        if op == 'grant':
            email = request.form.get('email', '').strip()
            admin_flag = request.form.get('is_admin', '') == '1'
            default_permission = request.form.get('default_permission', 'read')
            if default_permission not in _ADMIN_PERMISSIONS:
                error = 'Invalid default permission.'
            elif not email:
                error = 'Email is required.'
            else:
                try:
                    db.grant_user(
                        email,
                        is_admin=admin_flag,
                        default_permission=default_permission,
                        granted_by=session.get('username') or 'admin-ui',
                    )
                except ValueError as exc:
                    error = str(exc)
        elif op == 'revoke':
            email = request.form.get('email', '').strip()
            current = (session.get('email') or session.get('username') or '').strip().lower()
            if email.lower() == current:
                error = 'You cannot delete yourself.'
            elif not db.revoke_user(email):
                error = f'User does not exist: {email}'
        else:
            error = f'Unknown operation: {op}'
    users = db.list_users()
    return render_template(
        'admin_users.html',
        users=users,
        permissions=_ADMIN_PERMISSIONS,
        error=error,
        **ctx('', 'OAuth User Management'),
    )


def _collect_prefix_suggestions() -> dict:
    """Return separate folder and page lists for ACL prefix suggestions.

    Returned dict has keys:
      - 'folders': sorted folder paths (every non-leaf prefix of every page)
      - 'pages':   sorted page paths (.md file paths without extension)
      - 'all':     union, sorted (kept for server-side existence checks)
    """
    folders: set[str] = set()
    pages: set[str] = set()
    for page in get_all_pages():
        pages.add(page)
        parts = page.split('/')
        for i in range(1, len(parts)):
            folders.add('/'.join(parts[:i]))
    return {
        'folders': sorted(folders),
        'pages':   sorted(pages),
        'all':     sorted(folders | pages),
    }


def _admin_user_detail(email: str):
    denied = _require_admin_ui()
    if denied is not None:
        return denied
    email = email.strip().lower()
    error = ''
    if request.method == 'POST':
        if not validate_csrf():
            return _render_error(
                403,
                'Invalid submission',
                'Invalid submission (CSRF check failed).',
            )
        op = request.form.get('op', '')
        try:
            if op == 'update':
                permission = request.form.get('default_permission', 'read')
                admin_flag = request.form.get('is_admin', '') == '1'
                db.grant_user(
                    email,
                    is_admin=admin_flag,
                    default_permission=permission,
                    granted_by=session.get('username') or 'admin-ui',
                )
            elif op == 'path-grant':
                raw_prefix = request.form.get('prefix', '')
                permission = request.form.get('permission', 'read')
                allow_missing = request.form.get('allow_missing', '') == '1'
                normalized = db.normalize_prefix(raw_prefix)
                # Empty prefix means "vault root" and is always valid.
                if normalized and not allow_missing:
                    if normalized not in set(_collect_prefix_suggestions()['all']):
                        error = (
                            f'Path does not exist: {normalized!r}. '
                            'If this is intentional, enable "Allow non-existent paths" and submit again.'
                        )
                if not error:
                    db.upsert_user_path(
                        email,
                        raw_prefix,
                        permission,
                        granted_by=session.get('username') or 'admin-ui',
                    )
            elif op == 'path-revoke':
                prefix = request.form.get('prefix', '')
                db.delete_user_path(email, prefix)
            else:
                error = f'Unknown operation: {op}'
        except ValueError as exc:
            error = str(exc)

    user = db.get_user_by_email(email)
    if user is None:
        return _render_error(404, 'User Not Found', 'user not found')
    paths = db.list_user_paths(email)
    return render_template(
        'admin_user_detail.html',
        user=user,
        paths=paths,
        permissions=_ADMIN_PERMISSIONS,
        prefix_suggestions=_collect_prefix_suggestions(),
        error=error,
        **ctx('', f'Manage Permissions: {email}'),
    )


def _register_admin_routes() -> None:
    routes = [
        ('/_admin/users',                'admin_users',        _admin_users,        ['GET', 'POST']),
        ('/_admin/users/<path:email>',   'admin_user_detail',  _admin_user_detail,  ['GET', 'POST']),
    ]
    for path, endpoint, fn, methods in routes:
        if endpoint in app.view_functions:
            continue
        app.add_url_rule(path, endpoint=endpoint, view_func=fn, methods=methods)
        if config.URL_PREFIX:
            prefixed = 'prefixed_' + endpoint
            if prefixed not in app.view_functions:
                app.add_url_rule(
                    _with_url_prefix(path),
                    endpoint=prefixed,
                    view_func=fn,
                    methods=methods,
                )


_register_admin_routes()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _validate_markdown_scope() -> None:
    if not config.GIT_ROOT:
        return
    git_root = os.path.realpath(os.path.abspath(config.GIT_ROOT))
    markdown_root = os.path.realpath(os.path.abspath(config.MARKDOWN_DIR))
    if os.path.commonpath([git_root, markdown_root]) != git_root:
        raise RuntimeError(
            "Refusing to start: PWIKI_MARKDOWN_DIR must be inside PWIKI_GIT_ROOT "
            f"(PWIKI_GIT_ROOT={config.GIT_ROOT!r}, PWIKI_MARKDOWN_DIR={config.MARKDOWN_DIR!r})."
        )


def _startup():
    _configure_file_logging()
    logger.info(
        "pwiki startup markdown_dir={!r} exists={} is_dir={} read_only={} url_prefix={!r} file_io_log={}",
        config.MARKDOWN_DIR,
        os.path.exists(config.MARKDOWN_DIR),
        os.path.isdir(config.MARKDOWN_DIR),
        config.READ_ONLY,
        config.URL_PREFIX,
        config.FILE_IO_LOG,
    )
    _validate_markdown_scope()

    for d in [config.TEMP_DIR, config.DATA_DIR,
              config.HTML_DIR,
              config.UPLOAD_DIR, config.MARKDOWN_DIR]:
        create_dir(d)

    # Anonymous read-only mode must be opted into explicitly. Otherwise a
    # deployment that forgets to set GOOGLE_OAUTH_CLIENT_ID would silently
    # expose the entire vault.
    if not oauth_module.oauth_enabled() and not config.ALLOW_ANONYMOUS:
        raise RuntimeError(
            "Refusing to start: OAuth is not configured and PWIKI_ALLOW_ANONYMOUS "
            "is not set. Configure GOOGLE_OAUTH_CLIENT_ID/SECRET for production, "
            "or set PWIKI_ALLOW_ANONYMOUS=1 to explicitly allow anonymous "
            "read-only browsing."
        )

    # Refuse to run with the default dev secret when authenticated writes are
    # possible — that combination implies a real deployment.
    if config.SECRET_KEY == config.DEFAULT_DEV_SECRET:
        is_production_like = oauth_module.oauth_enabled() and not config.READ_ONLY
        if is_production_like:
            raise RuntimeError(
                "Refusing to start: PWIKI_SECRET_KEY is the default dev value. "
                "Set a strong random PWIKI_SECRET_KEY when OAuth is enabled "
                "and writes are allowed."
            )
        logger.warning(
            "pwiki startup using default PWIKI_SECRET_KEY (dev only). "
            "Set a strong PWIKI_SECRET_KEY for production."
        )

    # Initialize SQLite schema (users + permissions) and seed admin if configured.
    with db.connect():
        pass
    if config.ADMIN_GOOGLE_EMAIL and not db.get_user_by_email(config.ADMIN_GOOGLE_EMAIL):
        db.grant_user(
            config.ADMIN_GOOGLE_EMAIL,
            is_admin=True,
            default_permission='write',
            granted_by='bootstrap',
        )
        logger.info("pwiki bootstrap admin user seeded email={!r}", config.ADMIN_GOOGLE_EMAIL)

    if config.FILE_IO_LOG:
        pages = get_all_pages()
        logger.info("pwiki startup page sample count={} sample={}", len(pages), pages[:10])

    home_file = get_storage_page_file(config.HOME_PAGE)
    if not os.path.exists(home_file):
        if is_read_only():
            return
        create_dir(os.path.dirname(home_file))
        write_string_to_file(home_file, f"# {config.SITE_NAME}\n")

# Run startup for both gunicorn and python app.py.
_startup()

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    port  = int(os.environ.get('FLASK_PORT', '5000'))
    app.run(host='0.0.0.0', debug=debug, port=port)
