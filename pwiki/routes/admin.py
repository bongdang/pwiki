"""Admin UI routes (the `admin` blueprint): SQLite-backed user + path-ACL
management at `/_admin/users` and `/_admin/users/<email>`. Admin-only; gated by
`_require_admin_ui`, which redirects to the OAuth flow (or 503s when OAuth is
off) rather than silently exposing the panel.
"""

from flask import Blueprint, render_template, request, session

import config
import db
import oauth as oauth_module
from access import is_admin_allowed, validate_csrf
from storage import get_all_pages
from webutil import ctx, _login_redirect, _render_error, _with_url_prefix

bp = Blueprint('admin', __name__)

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


@bp.route('/_admin/users', methods=['GET', 'POST'])
def admin_users():
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


@bp.route('/_admin/users/<path:email>', methods=['GET', 'POST'])
def admin_user_detail(email: str):
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


def register_prefixed_routes(app) -> None:
    """Mirror the admin routes onto PWIKI_URL_PREFIX (matches legacy
    `_register_admin_routes` behavior)."""
    if not config.URL_PREFIX:
        return
    routes = [
        ('/_admin/users',              'prefixed_admin_users',       admin_users,       ['GET', 'POST']),
        ('/_admin/users/<path:email>', 'prefixed_admin_user_detail', admin_user_detail, ['GET', 'POST']),
    ]
    for path, endpoint, fn, methods in routes:
        if endpoint in app.view_functions:
            continue
        app.add_url_rule(_with_url_prefix(path), endpoint=endpoint, view_func=fn, methods=methods)
