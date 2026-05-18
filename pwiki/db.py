"""SQLite access for users + path-based permissions.

Schema is created on first use. ORM-free, stdlib sqlite3 only.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

import config


_PERMISSION_VALUES = ('none', 'read', 'write')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  email               TEXT PRIMARY KEY,
  sub                 TEXT UNIQUE,
  name                TEXT,
  is_admin            INTEGER NOT NULL DEFAULT 0,
  default_permission  TEXT NOT NULL DEFAULT 'read',
  created_at          TEXT NOT NULL,
  last_login_at       TEXT,
  granted_by          TEXT
);

CREATE TABLE IF NOT EXISTS user_paths (
  email       TEXT NOT NULL,
  prefix      TEXT NOT NULL,
  permission  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  granted_by  TEXT,
  PRIMARY KEY (email, prefix),
  FOREIGN KEY (email) REFERENCES users(email) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS users_sub ON users(sub) WHERE sub IS NOT NULL;
"""


_init_lock = threading.Lock()
_initialized: dict[str, bool] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_prefix(prefix: str) -> str:
    """Normalize a vault-relative path prefix.

    - trim surrounding whitespace
    - strip leading slashes
    - strip trailing slashes for single-node matching
    - an empty string means the whole vault
    """
    p = prefix.strip().replace('\\', '/')
    while p.startswith('/'):
        p = p[1:]
    while p.endswith('/'):
        p = p[:-1]
    return p


def _ensure_initialized(db_path: str) -> None:
    if _initialized.get(db_path):
        return
    with _init_lock:
        if _initialized.get(db_path):
            return
        parent = os.path.dirname(db_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _initialized[db_path] = True


def reset_initialized_cache() -> None:
    """Test helper: clear init cache so a changed DB path is initialized again."""
    _initialized.clear()


@contextmanager
def connect(db_path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    path = os.path.abspath(db_path or config.DB_PATH)
    _ensure_initialized(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def get_user_by_email(email: str) -> Optional[dict]:
    email = _normalize_email(email)
    with connect() as conn:
        row = conn.execute(
            'SELECT * FROM users WHERE email = ?', (email,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_sub(sub: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            'SELECT * FROM users WHERE sub = ?', (sub,)
        ).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            'SELECT * FROM users ORDER BY email'
        ).fetchall()
    return [dict(r) for r in rows]


def grant_user(
    email: str,
    *,
    is_admin: bool = False,
    default_permission: str = 'read',
    granted_by: Optional[str] = None,
) -> None:
    if default_permission not in _PERMISSION_VALUES:
        raise ValueError(f'invalid permission: {default_permission!r}')
    email = _normalize_email(email)
    if not email:
        raise ValueError('email required')
    now = _now()
    with connect() as conn:
        # Atomic upsert avoids a TOCTOU window between SELECT and INSERT/UPDATE.
        conn.execute(
            'INSERT INTO users (email, is_admin, default_permission, created_at, granted_by) '
            'VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT(email) DO UPDATE SET '
            '  is_admin = excluded.is_admin, '
            '  default_permission = excluded.default_permission, '
            '  granted_by = COALESCE(excluded.granted_by, users.granted_by)',
            (email, 1 if is_admin else 0, default_permission, now, granted_by),
        )


def revoke_user(email: str) -> bool:
    email = _normalize_email(email)
    with connect() as conn:
        cursor = conn.execute('DELETE FROM users WHERE email = ?', (email,))
    return cursor.rowcount > 0


def update_login(email: str, sub: str, name: Optional[str]) -> None:
    email = _normalize_email(email)
    with connect() as conn:
        conn.execute(
            'UPDATE users SET sub = ?, name = COALESCE(?, name), last_login_at = ? WHERE email = ?',
            (sub, name, _now(), email),
        )


def set_default_permission(email: str, permission: str) -> None:
    if permission not in _PERMISSION_VALUES:
        raise ValueError(f'invalid permission: {permission!r}')
    email = _normalize_email(email)
    with connect() as conn:
        conn.execute(
            'UPDATE users SET default_permission = ? WHERE email = ?',
            (permission, email),
        )


# ---------------------------------------------------------------------------
# Path override helpers
# ---------------------------------------------------------------------------

def list_user_paths(email: str) -> list[dict]:
    email = _normalize_email(email)
    with connect() as conn:
        rows = conn.execute(
            'SELECT prefix, permission, created_at, granted_by FROM user_paths '
            'WHERE email = ? ORDER BY prefix',
            (email,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_user_path(
    email: str,
    prefix: str,
    permission: str,
    *,
    granted_by: Optional[str] = None,
) -> None:
    if permission not in _PERMISSION_VALUES:
        raise ValueError(f'invalid permission: {permission!r}')
    email = _normalize_email(email)
    prefix = normalize_prefix(prefix)
    with connect() as conn:
        if not conn.execute(
            'SELECT email FROM users WHERE email = ?', (email,)
        ).fetchone():
            raise ValueError(f'user not found: {email}')
        conn.execute(
            'INSERT INTO user_paths (email, prefix, permission, created_at, granted_by) '
            'VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT(email, prefix) DO UPDATE SET '
            'permission = excluded.permission, granted_by = excluded.granted_by',
            (email, prefix, permission, _now(), granted_by),
        )


def delete_user_path(email: str, prefix: str) -> bool:
    email = _normalize_email(email)
    prefix = normalize_prefix(prefix)
    with connect() as conn:
        cursor = conn.execute(
            'DELETE FROM user_paths WHERE email = ? AND prefix = ?',
            (email, prefix),
        )
    return cursor.rowcount > 0
