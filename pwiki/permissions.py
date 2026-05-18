"""Permission resolution: prefix-based ACL on top of SQLite users/user_paths.

Matching rules:
- admins always bypass to 'write'
- longest prefix wins (most-specific wins)
- prefix matching only happens on path segment boundaries
- when no override matches, use user.default_permission
- unknown users resolve to 'none'
- global READ_ONLY does not change the resolved permission itself; routes block writes separately
"""

from __future__ import annotations

from typing import Optional

import db


PERMISSION_LEVELS = {'none': 0, 'read': 1, 'write': 2}


def _normalize_page_path(page_path: str) -> str:
    p = (page_path or '').strip().replace('\\', '/')
    while p.startswith('/'):
        p = p[1:]
    while p.endswith('/'):
        p = p[:-1]
    return p


def _prefix_matches(page_path: str, prefix: str) -> bool:
    """Return whether page_path belongs under prefix using segment boundaries.

    prefix='' matches the whole vault.
    prefix='notes' matches only 'notes' or 'notes/...', not 'notes-old'.
    """
    if prefix == '':
        return True
    if page_path == prefix:
        return True
    return page_path.startswith(prefix + '/')


def resolve_permission(email: Optional[str], page_path: str) -> str:
    """Return 'none'|'read'|'write' for the given user's page_path."""
    if not email:
        return 'none'
    user = db.get_user_by_email(email)
    if user is None:
        return 'none'
    overrides = db.list_user_paths(email)
    return resolve_permission_for_user(page_path, user, overrides)


def resolve_permission_for_user(page_path: str, user: dict, overrides: list) -> str:
    """Resolve permission from pre-loaded user/overrides without DB calls.

    Admins always resolve to 'write'. Otherwise the longest matching prefix wins.
    """
    if user.get('is_admin'):
        return 'write'
    page = _normalize_page_path(page_path)
    best = None
    for entry in overrides:
        prefix = entry['prefix']
        if not _prefix_matches(page, prefix):
            continue
        if best is None or len(prefix) > len(best['prefix']):
            best = entry
    if best is not None:
        return best['permission']
    return user.get('default_permission') or 'read'


def has_permission(email: Optional[str], page_path: str, level: str) -> bool:
    if level not in PERMISSION_LEVELS:
        raise ValueError(f'invalid level: {level!r}')
    actual = resolve_permission(email, page_path)
    return PERMISSION_LEVELS[actual] >= PERMISSION_LEVELS[level]


def filter_visible_paths(email: Optional[str], page_paths: list[str]) -> list[str]:
    """For sidebar/search: keep only paths with read-or-higher permission.

    Fetch user/overrides once to avoid N+1 DB queries.
    """
    if not email:
        return []
    user = db.get_user_by_email(email)
    if user is None:
        return []
    if user.get('is_admin'):
        return list(page_paths)
    overrides = db.list_user_paths(email)
    read_level = PERMISSION_LEVELS['read']
    return [
        p for p in page_paths
        if PERMISSION_LEVELS.get(resolve_permission_for_user(p, user, overrides), 0) >= read_level
    ]
