"""Vault file/directory storage layer.

Resolves and reads/writes Markdown pages under `config.MARKDOWN_DIR`, builds the
sidebar page tree, and filters it by per-user read permission. Writes go through
a same-directory temp file + `os.replace` so a failed write never truncates the
target. No Flask request/response handling lives here, but `flask.g` is used as a
per-request cache for the page list and is import-safe outside a request.
"""

import hashlib
import html
import os
import re
import tempfile
from typing import Literal, Optional, Tuple, TypedDict

from flask import g
from loguru import logger

import config
import db
import permissions


class TreeNode(TypedDict, total=False):
    name: str
    path: str
    type: Literal['dir', 'file']
    children: dict[str, 'TreeNode'] | list['TreeNode']
    active: bool
    open: bool


def _log_file_io(message: str, *args) -> None:
    if config.FILE_IO_LOG:
        logger.info(message, *args)


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
