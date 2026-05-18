"""C6: per-route permission enforcement when OAuth is enabled.

conftest.py sets OAuth env at import time, so oauth_enabled() is True. Each test
isolates its DB and vault, then preloads the session for its permission scenario.
"""

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
def auth_env(monkeypatch, tmp_path):
    md_root = tmp_path / 'vault'
    md_root.mkdir()
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', 'test-client-id')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', 'test-client-secret')
    db_path = str(tmp_path / 'pwiki.db')
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    db.reset_initialized_cache()
    # Sample pages
    (md_root / 'Public').mkdir()
    (md_root / 'Private').mkdir()
    (md_root / 'Home.md').write_text('# Home\n', encoding='utf-8')
    (md_root / 'Public' / 'Guide.md').write_text('# Guide\n', encoding='utf-8')
    (md_root / 'Private' / 'Diary.md').write_text('# Diary\n', encoding='utf-8')
    yield md_root
    db.reset_initialized_cache()


def _login(client, email, is_admin=False):
    with client.session_transaction() as sess:
        sess['username'] = email
        sess['email']    = email
        sess['is_admin'] = is_admin


def test_anonymous_browse_redirects_to_oauth_login(auth_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/Home', follow_redirects=False)
    assert resp.status_code == 302
    assert '/auth/google/login' in resp.headers.get('Location', '')


def test_read_allowed_when_default_read(auth_env):
    db.grant_user('alice@example.com', default_permission='read')
    client = pwiki_app.app.test_client()
    _login(client, 'alice@example.com')
    resp = client.get('/Home')
    assert resp.status_code == 200


def test_read_denied_when_default_none(auth_env):
    db.grant_user('bob@example.com', default_permission='none')
    client = pwiki_app.app.test_client()
    _login(client, 'bob@example.com')
    resp = client.get('/Home')
    assert resp.status_code == 403


def test_path_override_blocks_subtree(auth_env):
    db.grant_user('carol@example.com', default_permission='read')
    db.upsert_user_path('carol@example.com', 'Private', 'none')
    client = pwiki_app.app.test_client()
    _login(client, 'carol@example.com')
    assert client.get('/Public/Guide').status_code == 200
    assert client.get('/Private/Diary').status_code == 403


def test_write_blocked_without_write_permission(auth_env):
    db.grant_user('dave@example.com', default_permission='read')
    client = pwiki_app.app.test_client()
    _login(client, 'dave@example.com')
    resp = client.get('/Home?action=edit')
    assert resp.status_code == 403


def test_write_allowed_when_path_overrides_to_write(auth_env):
    db.grant_user('erin@example.com', default_permission='read')
    db.upsert_user_path('erin@example.com', 'Public', 'write')
    client = pwiki_app.app.test_client()
    _login(client, 'erin@example.com')
    resp = client.get('/Public/Guide?action=edit')
    assert resp.status_code == 200


def test_admin_bypasses_all_restrictions(auth_env):
    db.grant_user('admin@example.com', is_admin=True, default_permission='write')
    client = pwiki_app.app.test_client()
    _login(client, 'admin@example.com', is_admin=True)
    assert client.get('/Private/Diary').status_code == 200
    assert client.get('/Private/Diary?action=edit').status_code == 200


def test_read_only_global_kill_switch_blocks_write(auth_env, monkeypatch):
    monkeypatch.setattr(config, 'READ_ONLY', True)
    db.grant_user('felix@example.com', is_admin=True, default_permission='write')
    client = pwiki_app.app.test_client()
    _login(client, 'felix@example.com', is_admin=True)
    resp = client.get('/Home?action=edit')
    assert resp.status_code == 403


def test_search_filters_by_permission(auth_env):
    db.grant_user('hank@example.com', default_permission='read')
    db.upsert_user_path('hank@example.com', 'Private', 'none')
    client = pwiki_app.app.test_client()
    _login(client, 'hank@example.com')
    resp = client.get('/?action=search&q=Diary')
    assert resp.status_code == 200
    assert b'Private/Diary' not in resp.data
