"""A1 security regression tests."""

import os
import sys

import pytest

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

import config
import db
import app as pwiki_app


@pytest.fixture
def secure_env(monkeypatch, tmp_path):
    md_root = tmp_path / 'vault'
    md_root.mkdir()
    (md_root / 'Public').mkdir()
    (md_root / 'Public' / 'A.md').write_text('# A\n', encoding='utf-8')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', 'test-id')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', 'test-secret')
    db_path = str(tmp_path / 'pwiki.db')
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    db.reset_initialized_cache()
    yield md_root
    db.reset_initialized_cache()


def _login(client, email, is_admin=False):
    with client.session_transaction() as sess:
        sess['username'] = email
        sess['email']    = email
        sess['is_admin'] = is_admin


# ---------------------------------------------------------------------------
# A1#2 — _safe_oauth_next: protocol-relative / scheme / CRLF
# ---------------------------------------------------------------------------

def test_safe_oauth_next_rejects_protocol_relative():
    assert pwiki_app._safe_oauth_next('//evil.com/path') == '/'


def test_safe_oauth_next_rejects_backslash_escape():
    assert pwiki_app._safe_oauth_next('/\\evil.com') == '/'


def test_safe_oauth_next_rejects_url_encoded_slash_escape():
    assert pwiki_app._safe_oauth_next('/%2fevil') == '/'
    assert pwiki_app._safe_oauth_next('/%5cevil') == '/'


def test_safe_oauth_next_rejects_scheme_colon():
    assert pwiki_app._safe_oauth_next('/javascript:alert(1)') == '/'
    assert pwiki_app._safe_oauth_next('/data:text/html,x') == '/'


def test_safe_oauth_next_rejects_crlf():
    assert pwiki_app._safe_oauth_next('/path\r\nLocation:evil') == '/'


def test_safe_oauth_next_accepts_safe_path():
    assert pwiki_app._safe_oauth_next('/page?action=browse') == '/page?action=browse'


# ---------------------------------------------------------------------------
# A1#6 — do_preview must validate CSRF
# ---------------------------------------------------------------------------

def test_preview_rejects_missing_csrf(secure_env):
    db.grant_user('alice@example.com', default_permission='write')
    client = pwiki_app.app.test_client()
    _login(client, 'alice@example.com')
    resp = client.post('/Public/A?action=preview', data={'text': '# X'})
    assert resp.status_code == 403


def test_preview_accepts_valid_csrf(secure_env):
    db.grant_user('alice@example.com', default_permission='write')
    client = pwiki_app.app.test_client()
    _login(client, 'alice@example.com')
    with client.session_transaction() as sess:
        sess['csrf_token'] = 'tok'
    resp = client.post(
        '/Public/A?action=preview',
        data={'text': '# X', 'csrf_token': 'tok'},
    )
    assert resp.status_code == 200


def test_healthz_reports_markdown_dir_status(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))
    client = pwiki_app.app.test_client()

    resp = client.get('/healthz')

    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'ok'
    assert resp.get_json()['markdown_dir'] is True


def test_configure_file_logging_writes_rotating_log(monkeypatch, tmp_path):
    log_file = tmp_path / 'pwiki.log'
    monkeypatch.setattr(config, 'LOG_FILE', str(log_file))
    monkeypatch.setattr(config, 'LOG_ROTATION', '1 MB')
    monkeypatch.setattr(config, 'LOG_RETENTION', '1 day')

    pwiki_app._configure_file_logging()
    pwiki_app.logger.info('file logging smoke test')

    assert 'file logging smoke test' in log_file.read_text(encoding='utf-8')

    monkeypatch.setattr(config, 'LOG_FILE', '')
    pwiki_app._configure_file_logging()


def test_static_version_is_cached(monkeypatch, tmp_path):
    static_dir = tmp_path / 'static'
    static_dir.mkdir()
    (static_dir / 'app.js').write_text('x', encoding='utf-8')
    calls = 0
    original_getmtime = pwiki_app.os.path.getmtime

    def counted_getmtime(path):
        nonlocal calls
        calls += 1
        return original_getmtime(path)

    monkeypatch.setattr(pwiki_app.app, 'static_folder', str(static_dir))
    monkeypatch.setattr(pwiki_app.os.path, 'getmtime', counted_getmtime)
    pwiki_app._cached_static_version.cache_clear()

    assert pwiki_app._static_version('app.js') == pwiki_app._static_version('app.js')
    assert calls == 1

    pwiki_app._cached_static_version.cache_clear()


def test_static_version_bypasses_cache_in_debug(monkeypatch, tmp_path):
    static_dir = tmp_path / 'static'
    static_dir.mkdir()
    (static_dir / 'app.js').write_text('x', encoding='utf-8')
    calls = 0
    original_getmtime = pwiki_app.os.path.getmtime

    def counted_getmtime(path):
        nonlocal calls
        calls += 1
        return original_getmtime(path)

    monkeypatch.setattr(pwiki_app.app, 'static_folder', str(static_dir))
    monkeypatch.setattr(pwiki_app.os.path, 'getmtime', counted_getmtime)
    monkeypatch.setattr(pwiki_app.app, 'debug', True)
    pwiki_app._cached_static_version.cache_clear()

    assert pwiki_app._static_version('app.js') == pwiki_app._static_version('app.js')
    assert calls == 2

    pwiki_app._cached_static_version.cache_clear()


# ---------------------------------------------------------------------------
# A1#8 — attachment path normalization
# ---------------------------------------------------------------------------

def test_attachment_path_traversal_denied(secure_env):
    db.grant_user('bob@example.com', default_permission='read')
    db.upsert_user_path('bob@example.com', 'Public', 'read')
    client = pwiki_app.app.test_client()
    _login(client, 'bob@example.com')
    # `images/../etc/passwd` should not slide past the ACL by claiming an
    # `images/` parent prefix.
    resp = client.get('/attach/images/../etc/passwd')
    assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# A1#9 — startup refuses default SECRET_KEY in production-like config
# ---------------------------------------------------------------------------

def test_startup_refuses_default_secret_in_production_like_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SECRET_KEY', config.DEFAULT_DEV_SECRET)
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', 'real-id')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', 'real-secret')
    monkeypatch.setattr(config, 'READ_ONLY', False)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path / 'vault'))
    monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / 'pwiki.db'))
    db.reset_initialized_cache()
    with pytest.raises(RuntimeError):
        pwiki_app._startup()


def test_startup_allows_default_secret_in_read_only_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SECRET_KEY', config.DEFAULT_DEV_SECRET)
    monkeypatch.setattr(config, 'READ_ONLY', True)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path / 'vault'))
    monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / 'pwiki.db'))
    db.reset_initialized_cache()
    pwiki_app._startup()  # must not raise


def test_startup_refuses_anonymous_without_optin(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', '')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', '')
    monkeypatch.setattr(config, 'ALLOW_ANONYMOUS', False)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path / 'vault'))
    monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / 'pwiki.db'))
    db.reset_initialized_cache()
    with pytest.raises(RuntimeError, match='PWIKI_ALLOW_ANONYMOUS'):
        pwiki_app._startup()


def test_startup_allows_anonymous_with_optin(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', '')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', '')
    monkeypatch.setattr(config, 'ALLOW_ANONYMOUS', True)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path / 'vault'))
    monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / 'pwiki.db'))
    db.reset_initialized_cache()
    pwiki_app._startup()  # must not raise


def test_startup_refuses_markdown_dir_outside_git_root(monkeypatch, tmp_path):
    git_root = tmp_path / 'repo'
    markdown_root = tmp_path / 'outside'
    monkeypatch.setattr(config, 'GIT_ROOT', str(git_root))
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(markdown_root))

    with pytest.raises(RuntimeError, match='PWIKI_MARKDOWN_DIR must be inside PWIKI_GIT_ROOT'):
        pwiki_app._startup()


def test_startup_allows_markdown_dir_inside_git_root(monkeypatch, tmp_path):
    git_root = tmp_path / 'repo'
    markdown_root = git_root / 'team' / 'wiki'
    monkeypatch.setattr(config, 'GIT_ROOT', str(git_root))
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(markdown_root))
    monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / 'pwiki.db'))
    db.reset_initialized_cache()

    pwiki_app._startup()


# ---------------------------------------------------------------------------
# A3 — admin route fallback when OAuth disabled
# ---------------------------------------------------------------------------

def test_admin_route_returns_503_when_oauth_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', '')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', '')
    db.reset_initialized_cache()
    client = pwiki_app.app.test_client()
    resp = client.get('/_admin/users', follow_redirects=False)
    assert resp.status_code == 503


def test_oauth_logout_ignores_cross_origin_referrer(secure_env, monkeypatch):
    db.grant_user('lily@example.com')
    client = pwiki_app.app.test_client()
    _login(client, 'lily@example.com')
    resp = client.get(
        '/auth/logout',
        headers={'Referer': 'https://evil.example.com/x'},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers.get('Location', '')
    assert 'evil.example.com' not in location
