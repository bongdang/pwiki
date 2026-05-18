"""C7: UI visibility tests.

- When OAuth is enabled, unauthorized pages disappear from the sidebar.
- Without write permission, edit/new-page/delete buttons are hidden.
- When OAuth is enabled, the login page shows the Google login button.
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
def oauth_ui(monkeypatch, tmp_path):
    md_root = tmp_path / 'vault'
    md_root.mkdir()
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', 'test-id')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', 'test-secret')
    db_path = str(tmp_path / 'pwiki.db')
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    db.reset_initialized_cache()

    (md_root / 'Public').mkdir()
    (md_root / 'Private').mkdir()
    (md_root / 'Home.md').write_text('# Home\n', encoding='utf-8')
    (md_root / 'Public' / 'A.md').write_text('# A\n', encoding='utf-8')
    (md_root / 'Private' / 'B.md').write_text('# B\n', encoding='utf-8')
    yield md_root
    db.reset_initialized_cache()


def _login(client, email, is_admin=False):
    with client.session_transaction() as sess:
        sess['username'] = email
        sess['email']    = email
        sess['is_admin'] = is_admin


def test_sidebar_hides_unauthorized_subtree(oauth_ui):
    db.grant_user('alice@example.com', default_permission='read')
    db.upsert_user_path('alice@example.com', 'Private', 'none')
    client = pwiki_app.app.test_client()
    _login(client, 'alice@example.com')
    resp = client.get('/Home')
    body = resp.data.decode('utf-8')
    assert 'Public' in body
    assert 'Private' not in body


def test_edit_button_hidden_when_no_write(oauth_ui):
    db.grant_user('bob@example.com', default_permission='read')
    client = pwiki_app.app.test_client()
    _login(client, 'bob@example.com')
    resp = client.get('/Home')
    body = resp.data.decode('utf-8')
    assert '?action=edit' not in body


def test_edit_button_visible_when_write(oauth_ui):
    db.grant_user('carol@example.com', default_permission='write')
    client = pwiki_app.app.test_client()
    _login(client, 'carol@example.com')
    resp = client.get('/Home')
    body = resp.data.decode('utf-8')
    assert '?action=edit' in body


def test_delete_button_only_for_admin(oauth_ui):
    db.grant_user('dave@example.com', default_permission='write')
    client = pwiki_app.app.test_client()
    _login(client, 'dave@example.com')
    resp = client.get('/Home')
    body = resp.data.decode('utf-8')
    assert '?action=delete' not in body

    db.grant_user('admin@example.com', is_admin=True, default_permission='write')
    _login(client, 'admin@example.com', is_admin=True)
    resp = client.get('/Home')
    body = resp.data.decode('utf-8')
    assert '?action=delete' in body


def test_admin_user_can_reach_admin_users(oauth_ui):
    db.grant_user('admin@example.com', is_admin=True, default_permission='write')
    client = pwiki_app.app.test_client()
    _login(client, 'admin@example.com', is_admin=True)
    resp = client.get('/_admin/users')
    assert resp.status_code == 200
    assert b'OAuth' in resp.data


def test_landing_redirects_to_index_md_when_home_unreadable(oauth_ui, monkeypatch):
    monkeypatch.setattr(config, 'HOME_PAGE', 'MyHome')  # not present in vault
    (oauth_ui / 'Public' / 'index.md').write_text('# index\n', encoding='utf-8')
    db.grant_user('alice@example.com', default_permission='none')
    db.upsert_user_path('alice@example.com', 'Public', 'read')
    client = pwiki_app.app.test_client()
    _login(client, 'alice@example.com')
    resp = client.get('/', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/Public/index')


def test_landing_finds_index_at_deepest_granted_folder(oauth_ui, monkeypatch):
    monkeypatch.setattr(config, 'HOME_PAGE', 'MyHome')
    (oauth_ui / 'Recordings').mkdir()
    (oauth_ui / 'Recordings' / 'Daily').mkdir()
    (oauth_ui / 'Recordings' / 'Daily' / 'index.md').write_text('# Daily index\n', encoding='utf-8')
    (oauth_ui / 'Recordings' / 'Daily' / 'A.md').write_text('# A\n', encoding='utf-8')
    db.grant_user('eve@example.com', default_permission='none')
    db.upsert_user_path('eve@example.com', 'Recordings/Daily', 'read')
    client = pwiki_app.app.test_client()
    _login(client, 'eve@example.com')
    resp = client.get('/', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/Recordings/Daily/index')


def test_landing_redirects_to_first_file_when_no_index(oauth_ui, monkeypatch):
    monkeypatch.setattr(config, 'HOME_PAGE', 'MyHome')
    db.grant_user('bob@example.com', default_permission='none')
    db.upsert_user_path('bob@example.com', 'Public', 'read')
    client = pwiki_app.app.test_client()
    _login(client, 'bob@example.com')
    resp = client.get('/', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/Public/A')


def test_landing_shows_no_access_message_when_nothing_readable(oauth_ui, monkeypatch):
    monkeypatch.setattr(config, 'HOME_PAGE', 'MyHome')
    db.grant_user('carol@example.com', default_permission='none')
    client = pwiki_app.app.test_client()
    _login(client, 'carol@example.com')
    resp = client.get('/', follow_redirects=False)
    assert resp.status_code == 403
    assert 'You do not have access to any pages'.encode() in resp.data


def test_landing_direct_home_url_still_forbidden(oauth_ui, monkeypatch):
    monkeypatch.setattr(config, 'HOME_PAGE', 'MyHome')
    (oauth_ui / 'MyHome.md').write_text('# MyHome\n', encoding='utf-8')
    db.grant_user('dan@example.com', default_permission='none')
    db.upsert_user_path('dan@example.com', 'Public', 'read')
    client = pwiki_app.app.test_client()
    _login(client, 'dan@example.com')
    resp = client.get('/MyHome', follow_redirects=False)
    assert resp.status_code == 403


def test_admin_gear_only_for_admin(oauth_ui):
    db.grant_user('eve@example.com', default_permission='read')
    client = pwiki_app.app.test_client()
    _login(client, 'eve@example.com')
    body = client.get('/Home').data.decode('utf-8')
    assert 'rail-admin' not in body

    db.grant_user('admin@example.com', is_admin=True, default_permission='write')
    _login(client, 'admin@example.com', is_admin=True)
    body = client.get('/Home').data.decode('utf-8')
    assert 'rail-admin' in body
    assert '/_admin/users' in body
