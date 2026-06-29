"""Authentication, CSRF, and permission predicates.

Pure decision helpers with no response rendering: they answer "is this allowed?"
so the route layer and the response helpers in `webutil` can act on the result.
Active enforcement (rendering a 403 / login redirect) lives in `webutil`, which
imports this module — keep the dependency one-way to avoid an import cycle.
"""

import secrets
from typing import Optional

from flask import request, session

import config
import oauth as oauth_module
import permissions


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


def is_edit_allowed() -> bool:
    return bool(session.get('username'))


def is_admin_allowed() -> bool:
    return bool(session.get('is_admin'))


def is_read_only() -> bool:
    return bool(config.READ_ONLY)


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
