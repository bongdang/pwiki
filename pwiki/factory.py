"""Application factory.

`create_app()` builds the Flask app: config, the CSRF Jinja global, the page /
admin / oauth blueprints, the internal read-only API, the security-header and
413 hooks, the PWIKI_URL_PREFIX route mirrors, then runs startup validation.

Kept as a flat module (not `pwiki/__init__.py`) so the Docker image — which
flattens `pwiki/` to `/app` and runs `gunicorn app:app` — can import it. The
static-version helpers stay in `app.py` because tests monkeypatch `app.app`.
"""

import os
from typing import Optional

from flask import Flask, jsonify, request
from loguru import logger

import config
import db
import oauth as oauth_module
from access import generate_csrf_token, is_read_only
from internal_api import register_internal_api
from routes import admin as admin_routes
from routes import oauth as oauth_routes
from routes import pages as pages_routes
from storage import (
    create_dir,
    get_all_pages,
    get_storage_page_file,
    write_string_to_file,
)
from webutil import json_error, _render_error

_log_file_sink_id: Optional[int] = None
_log_file_sink_path: Optional[str] = None


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


def _handle_request_too_large(_error):
    """Werkzeug raises 413 when the body exceeds MAX_CONTENT_LENGTH (e.g. an
    oversized upload). Reply in JSON for AJAX uploads (which send
    `Accept: application/json`) and with the shared error page otherwise.
    """
    limit_mb = config.MAX_CONTENT_LENGTH / (1024 * 1024)
    message = f'The upload is too large (limit {limit_mb:.0f} MB).'
    if request.accept_mimetypes.best == 'application/json':
        return json_error(message, 413)
    return _render_error(413, 'Upload too large', message)


def _set_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return response


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


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE=config.SESSION_COOKIE_SAMESITE,
        SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
        # Werkzeug automatically returns 413 RequestEntityTooLarge when this is exceeded.
        MAX_CONTENT_LENGTH=config.MAX_CONTENT_LENGTH,
    )
    app.jinja_env.globals['csrf_token'] = generate_csrf_token

    app.register_error_handler(413, _handle_request_too_large)
    app.after_request(_set_security_headers)

    @app.context_processor
    def _inject_static_version():
        """Expose `static_version(filename)` -> file mtime so templates can append
        `?v=<mtime>` to static asset URLs and bust the browser cache when the
        source file changes. Imported lazily (at request time, when the `app`
        module is fully loaded) so the helper can keep referencing the
        module-global `app` that `test_static_version_*` monkeypatch, without
        creating a factory <-> app import cycle.
        """
        from app import _static_version
        return {'static_version': _static_version}

    # Canonical routes via blueprints.
    app.register_blueprint(pages_routes.bp)
    app.register_blueprint(admin_routes.bp)
    oauth_module.init_oauth(app)
    if oauth_module.oauth_enabled():
        app.register_blueprint(oauth_routes.bp)

    # Internal read-only API for same-host / private-network consumers.
    register_internal_api(app)

    # Mirror routes onto PWIKI_URL_PREFIX when configured.
    pages_routes.register_prefixed_routes(app)
    admin_routes.register_prefixed_routes(app)
    if oauth_module.oauth_enabled():
        oauth_routes.register_prefixed_routes(app)

    _startup()
    return app
