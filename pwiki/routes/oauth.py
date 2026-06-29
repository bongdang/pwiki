"""Google OAuth login/callback/logout routes (the `oauth` blueprint).

Registered only when OAuth is configured (see `factory.create_app`). Redirect
targets are validated by `_safe_oauth_next` so a spoofed `next`/`Referer` cannot
bounce the user off-origin.
"""

from typing import Optional
from urllib.parse import urlparse

from flask import Blueprint, abort, redirect, request, session
from loguru import logger

import config
import db
import oauth as oauth_module
from webutil import _render_forbidden, _with_url_prefix

bp = Blueprint('oauth', __name__)


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


@bp.route('/auth/google/login', methods=['GET'])
def oauth_login():
    if not oauth_module.oauth_enabled():
        return "Google OAuth is not configured.", 503
    next_url = _safe_oauth_next(request.args.get('next', ''))
    session['oauth_next'] = next_url
    redirect_uri = _build_oauth_redirect_uri()
    return oauth_module.get_oauth().google.authorize_redirect(redirect_uri)


@bp.route('/auth/google/callback', methods=['GET'])
def oauth_callback():
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


@bp.route('/auth/logout', methods=['GET', 'POST'])
def oauth_logout():
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


def register_prefixed_routes(app) -> None:
    """Mirror the OAuth routes onto PWIKI_URL_PREFIX (matches legacy
    `_init_oauth` behavior)."""
    if not config.URL_PREFIX:
        return
    routes = [
        ('/auth/google/login',    'prefixed_oauth_google_login',    oauth_login,    ['GET']),
        ('/auth/google/callback', 'prefixed_oauth_google_callback', oauth_callback, ['GET']),
        ('/auth/logout',          'prefixed_oauth_logout',          oauth_logout,   ['GET', 'POST']),
    ]
    for path, endpoint, fn, methods in routes:
        if endpoint in app.view_functions:
            continue
        app.add_url_rule(_with_url_prefix(path), endpoint=endpoint, view_func=fn, methods=methods)
