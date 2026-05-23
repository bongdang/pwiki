"""Internal read-only API for same-host / private-network consumers.

Separate from the OAuth web UI: this surface is intended for an internal AI
assistant or other backend process that needs to search / read Markdown
documents without going through Google login. It is **read-only** by design —
none of the handlers write, create, or delete files.

Layered defenses, in request order:

  1. Activation guard — if ``PWIKI_INTERNAL_API_TOKEN`` is empty the whole
     ``/api/internal/*`` surface returns 404 so an unconfigured deployment
     cannot leak data.
  2. CIDR allowlist — the resolved client IP must fall within
     ``PWIKI_INTERNAL_API_ALLOWED_CIDRS`` (defaults to loopback + RFC1918).
  3. Bearer token — ``Authorization: Bearer <token>`` checked with
     ``hmac.compare_digest`` (timing-safe).

Client IP resolution: ``request.remote_addr`` is used directly unless
``PWIKI_INTERNAL_API_TRUSTED_PROXY_CIDRS`` is set and the immediate peer
sits inside one of those CIDRs — only then is the last hop of
``X-Forwarded-For`` honored. Without that opt-in, a malicious client could
spoof the header and bypass the CIDR allowlist, so the default is paranoid.

Filesystem safety: every requested path is resolved with ``os.path.realpath``
and compared against the realpath of ``MARKDOWN_DIR`` via
``os.path.commonpath`` before any read. Traversal segments (``..``) are
rejected up front; symlinks that point outside the vault are caught by the
realpath check.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from datetime import datetime
from functools import wraps
from typing import Iterable, Optional

from flask import jsonify, request
from loguru import logger

import config


DEFAULT_ALLOWED_CIDRS = '127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16'

SEARCH_DEFAULT_LIMIT = 20
SEARCH_MAX_LIMIT = 100
SNIPPET_RADIUS = 80

FOLDER_DEFAULT_LIMIT = 100
FOLDER_MAX_LIMIT = 500


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def _enabled() -> bool:
    return bool((getattr(config, 'INTERNAL_API_TOKEN', '') or '').strip())


def _parse_cidrs(raw: str) -> list:
    nets = []
    for part in (raw or '').split(','):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            nets.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            # Bad entries should not silently widen the allowlist — log and skip.
            logger.warning("internal_api: ignoring invalid CIDR {!r}", candidate)
    return nets


def _allowed_cidrs() -> list:
    raw = getattr(config, 'INTERNAL_API_ALLOWED_CIDRS', '') or DEFAULT_ALLOWED_CIDRS
    return _parse_cidrs(raw)


def _trusted_proxy_cidrs() -> list:
    return _parse_cidrs(getattr(config, 'INTERNAL_API_TRUSTED_PROXY_CIDRS', ''))


def _ip_in_any(addr: str, nets: Iterable) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in nets)


def _client_ip() -> Optional[str]:
    """Return the client IP used for CIDR evaluation.

    Honors ``X-Forwarded-For`` only when ``remote_addr`` is inside the
    trusted-proxy CIDR list. The last entry of XFF is the address recorded
    by the closest trusted proxy, so it is the most trustworthy hop.
    """
    remote = request.remote_addr
    if not remote:
        return None
    trusted = _trusted_proxy_cidrs()
    if trusted and _ip_in_any(remote, trusted):
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            candidate = forwarded.split(',')[-1].strip()
            if candidate:
                return candidate
    return remote


def _error(code: str, message: str, status: int):
    response = jsonify({'error': {'code': code, 'message': message}})
    response.status_code = status
    return response


def _guard():
    if not _enabled():
        return _error('not_configured', 'Internal API is disabled.', 404)
    addr = _client_ip()
    if not addr or not _ip_in_any(addr, _allowed_cidrs()):
        return _error('forbidden_cidr', 'Client IP is not in the allowed CIDR list.', 403)
    expected = (config.INTERNAL_API_TOKEN or '').strip()
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return _error('unauthorized', 'Missing or malformed Authorization header.', 401)
    submitted = header[len('Bearer '):].strip()
    if not submitted or not hmac.compare_digest(submitted, expected):
        return _error('unauthorized', 'Invalid internal API token.', 401)
    return None


def _require_internal_api(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        denied = _guard()
        if denied is not None:
            return denied
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _vault_root() -> str:
    return os.path.realpath(os.path.abspath(config.MARKDOWN_DIR))


def _rel_path(target: str) -> str:
    return os.path.relpath(target, _vault_root()).replace(os.sep, '/')


def _is_under_root(target: str) -> bool:
    root = _vault_root()
    try:
        return os.path.commonpath([root, os.path.realpath(target)]) == root
    except ValueError:
        return False


def _resolve_under_root(rel_path: str) -> Optional[str]:
    """Resolve a vault-relative path. Returns the realpath of the target, or
    None when it escapes the vault via traversal, an absolute path component,
    or a symlink target outside the root.
    """
    root = _vault_root()
    rel = (rel_path or '').replace('\\', '/').strip().lstrip('/')
    if not rel:
        return root
    parts = [part for part in rel.split('/') if part and part != '.']
    if any(part == '..' for part in parts):
        return None
    joined = os.path.join(root, *parts)
    target = os.path.realpath(joined)
    try:
        if os.path.commonpath([root, target]) != root:
            return None
    except ValueError:
        return None
    return target


def _format_mtime(path: str) -> Optional[str]:
    try:
        ts = os.path.getmtime(path)
    except OSError:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def _iter_markdown_pages():
    """Yield (rel_path, abs_path) for non-hidden .md files inside the vault.
    Symlinks whose realpath escapes the vault are skipped defensively even
    though ``os.walk`` already does not descend into symlinked directories.
    """
    root = _vault_root()
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for filename in sorted(filenames):
            if filename.startswith('.') or not filename.endswith('.md'):
                continue
            full = os.path.join(dirpath, filename)
            if not _is_under_root(full):
                continue
            yield _rel_path(full), full


def _read_markdown_text(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError as exc:
        logger.warning("internal_api: read failed path={!r} error={!r}", path, exc)
        return ''


def _parse_limit(value: Optional[str], default: int, maximum: int) -> int:
    if value is None or value == '':
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, maximum)


def _parse_bool(value: Optional[str]) -> bool:
    return (value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _title_from_rel(rel: str) -> str:
    base = os.path.basename(rel)
    return base[:-3] if base.endswith('.md') else base


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@_require_internal_api
def api_health():
    return jsonify({
        'ok': True,
        'service': 'pwiki',
        'internalApi': True,
    })


@_require_internal_api
def api_search():
    query = (request.args.get('q', '') or '').strip()
    if not query:
        return _error('bad_request', 'Query parameter "q" is required.', 400)
    limit = _parse_limit(request.args.get('limit'), SEARCH_DEFAULT_LIMIT, SEARCH_MAX_LIMIT)

    query_lower = query.lower()
    results = []
    for rel, full in _iter_markdown_pages():
        if len(results) >= limit:
            break
        title_match = query_lower in rel.lower()
        text = _read_markdown_text(full)
        text_lower = text.lower()
        idx = text_lower.find(query_lower)
        if not title_match and idx < 0:
            continue
        if idx >= 0:
            start = max(0, idx - SNIPPET_RADIUS)
            end = min(len(text), idx + len(query) + SNIPPET_RADIUS)
            snippet = (
                ('...' if start > 0 else '')
                + text[start:end]
                + ('...' if end < len(text) else '')
            )
        else:
            head = text[: SNIPPET_RADIUS * 2]
            snippet = head + ('...' if len(text) > len(head) else '')
        results.append({
            'path': rel,
            'title': _title_from_rel(rel),
            'snippet': snippet,
            'modifiedTime': _format_mtime(full),
        })
    return jsonify({
        'query': query,
        'count': len(results),
        'results': results,
    })


@_require_internal_api
def api_page():
    raw = request.args.get('path', '')
    if not raw or not raw.strip():
        return _error('bad_request', 'Query parameter "path" is required.', 400)
    if not raw.lower().endswith('.md'):
        return _error('bad_request', 'Only .md files are allowed.', 400)
    target = _resolve_under_root(raw)
    if target is None:
        return _error('forbidden_path', 'Path resolves outside the vault root.', 403)
    if not os.path.isfile(target):
        return _error('not_found', 'Page not found.', 404)
    rel = _rel_path(target)
    return jsonify({
        'path': rel,
        'title': _title_from_rel(rel),
        'content': _read_markdown_text(target),
        'modifiedTime': _format_mtime(target),
    })


@_require_internal_api
def api_folder():
    raw = request.args.get('path', '')
    recursive = _parse_bool(request.args.get('recursive'))
    limit = _parse_limit(request.args.get('limit'), FOLDER_DEFAULT_LIMIT, FOLDER_MAX_LIMIT)
    target = _resolve_under_root(raw)
    if target is None:
        return _error('forbidden_path', 'Path resolves outside the vault root.', 403)
    if not os.path.isdir(target):
        return _error('not_found', 'Folder not found.', 404)
    items = _collect_folder_items(target, recursive=recursive, limit=limit)
    rel = '' if target == _vault_root() else _rel_path(target)
    return jsonify({
        'path': rel,
        'count': len(items),
        'items': items,
    })


def _collect_folder_items(folder: str, *, recursive: bool, limit: int) -> list:
    items: list = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
            for d in list(dirnames):
                full_dir = os.path.join(dirpath, d)
                if not _is_under_root(full_dir):
                    continue
                items.append({'type': 'folder', 'path': _rel_path(full_dir)})
                if len(items) >= limit:
                    return items
            for filename in sorted(filenames):
                if filename.startswith('.') or not filename.endswith('.md'):
                    continue
                full = os.path.join(dirpath, filename)
                if not _is_under_root(full):
                    continue
                items.append({
                    'type': 'file',
                    'path': _rel_path(full),
                    'title': _title_from_rel(filename),
                    'modifiedTime': _format_mtime(full),
                })
                if len(items) >= limit:
                    return items
        return items

    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return items
    for name in entries:
        if name.startswith('.'):
            continue
        full = os.path.join(folder, name)
        if not _is_under_root(full):
            continue
        if os.path.isdir(full):
            items.append({'type': 'folder', 'path': _rel_path(full)})
        elif os.path.isfile(full) and name.endswith('.md'):
            items.append({
                'type': 'file',
                'path': _rel_path(full),
                'title': _title_from_rel(name),
                'modifiedTime': _format_mtime(full),
            })
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_ROUTES = (
    ('/api/internal/health', 'internal_api_health', api_health),
    ('/api/internal/search', 'internal_api_search', api_search),
    ('/api/internal/page',   'internal_api_page',   api_page),
    ('/api/internal/folder', 'internal_api_folder', api_folder),
)


def register_internal_api(app) -> None:
    """Register the internal API routes on the given Flask app.

    Idempotent. Also adds copies under the configured ``URL_PREFIX`` so that
    a reverse-proxy sub-path deployment can reach the API at
    ``/<prefix>/api/internal/...`` if desired.
    """
    prefix = getattr(config, 'URL_PREFIX', '') or ''
    for path, endpoint, view in _ROUTES:
        if endpoint not in app.view_functions:
            app.add_url_rule(path, endpoint=endpoint, view_func=view, methods=['GET'])
        if prefix:
            prefixed_endpoint = 'prefixed_' + endpoint
            if prefixed_endpoint not in app.view_functions:
                app.add_url_rule(
                    f'{prefix}{path}',
                    endpoint=prefixed_endpoint,
                    view_func=view,
                    methods=['GET'],
                )
