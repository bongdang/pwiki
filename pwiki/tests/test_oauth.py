import os
import sys

import pytest

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

import config
import db
import oauth as oauth_module
import app as pwiki_app


class _FakeGoogleClient:
    def __init__(self, token):
        self._token = token
        self.last_redirect_uri = None

    def authorize_access_token(self):
        return self._token

    def authorize_redirect(self, redirect_uri):
        self.last_redirect_uri = redirect_uri
        from flask import redirect
        return redirect('https://accounts.google.com/o/oauth2/v2/auth?fake=1')


class _FakeOAuth:
    def __init__(self, token):
        self.google = _FakeGoogleClient(token)


@pytest.fixture
def oauth_env(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'pwiki.db')
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', 'test-client-id')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', 'test-client-secret')
    monkeypatch.setattr(config, 'PUBLIC_BASE_URL', '')
    db.reset_initialized_cache()
    yield
    db.reset_initialized_cache()


def _install_fake_oauth(monkeypatch, token):
    fake = _FakeOAuth(token)
    monkeypatch.setattr(oauth_module, '_oauth', fake)
    return fake


def test_oauth_enabled_flag(monkeypatch):
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', '')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', '')
    assert not oauth_module.oauth_enabled()
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', 'cid')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', 'csec')
    assert oauth_module.oauth_enabled()


def test_login_route_returns_503_when_disabled(oauth_env, monkeypatch):
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_ID', '')
    monkeypatch.setattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET', '')
    client = pwiki_app.app.test_client()
    resp = client.get('/auth/google/login')
    assert resp.status_code == 503


def test_parse_userinfo_extracts_claims():
    token = {'userinfo': {'sub': 's', 'email': 'A@Example.com', 'name': 'A'}}
    info = oauth_module.parse_userinfo(token)
    assert info == {'sub': 's', 'email': 'a@example.com', 'name': 'A'}


def test_parse_userinfo_rejects_missing():
    with pytest.raises(ValueError):
        oauth_module.parse_userinfo({'userinfo': {'email': 'a@b.com'}})
    with pytest.raises(ValueError):
        oauth_module.parse_userinfo({'userinfo': {'sub': 's'}})


def test_parse_userinfo_rejects_unverified_email():
    with pytest.raises(ValueError, match='email is not verified'):
        oauth_module.parse_userinfo({
            'userinfo': {
                'sub': 's',
                'email': 'a@example.com',
                'email_verified': False,
            }
        })


def test_callback_authorized_user_sets_session(oauth_env, monkeypatch):
    db.grant_user('alice@example.com', default_permission='read')
    _install_fake_oauth(monkeypatch, {
        'userinfo': {'sub': 'sub-alice', 'email': 'alice@example.com', 'name': 'Alice'}
    })

    client = pwiki_app.app.test_client()
    resp = client.get('/auth/google/callback', follow_redirects=False)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get('username') == 'alice@example.com'
        assert sess.get('email') == 'alice@example.com'
        assert sess.get('is_admin') is False

    user = db.get_user_by_sub('sub-alice')
    assert user is not None
    assert user['email'] == 'alice@example.com'
    assert user['name'] == 'Alice'
    assert user['last_login_at']


def test_callback_clears_existing_csrf_token(oauth_env, monkeypatch):
    db.grant_user('csrf@example.com', default_permission='read')
    _install_fake_oauth(monkeypatch, {
        'userinfo': {'sub': 'sub-csrf', 'email': 'csrf@example.com', 'name': 'C'}
    })

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['csrf_token'] = 'old-token'

    resp = client.get('/auth/google/callback', follow_redirects=False)

    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert 'csrf_token' not in sess


def test_callback_admin_sets_admin_flag(oauth_env, monkeypatch):
    db.grant_user('admin@example.com', is_admin=True, default_permission='write')
    _install_fake_oauth(monkeypatch, {
        'userinfo': {'sub': 'sub-admin', 'email': 'admin@example.com', 'name': 'Adm'}
    })

    client = pwiki_app.app.test_client()
    resp = client.get('/auth/google/callback', follow_redirects=False)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get('is_admin') is True


def test_callback_unauthorized_email_returns_403(oauth_env, monkeypatch):
    _install_fake_oauth(monkeypatch, {
        'userinfo': {'sub': 'sub-x', 'email': 'stranger@example.com', 'name': 'X'}
    })

    client = pwiki_app.app.test_client()
    resp = client.get('/auth/google/callback', follow_redirects=False)
    assert resp.status_code == 403


def test_callback_pending_user_gets_sub_filled(oauth_env, monkeypatch):
    """A pending email-only grant should receive sub on first callback."""
    db.grant_user('bob@example.com')
    user = db.get_user_by_email('bob@example.com')
    assert user['sub'] is None

    _install_fake_oauth(monkeypatch, {
        'userinfo': {'sub': 'sub-bob', 'email': 'bob@example.com', 'name': 'Bob'}
    })

    client = pwiki_app.app.test_client()
    resp = client.get('/auth/google/callback', follow_redirects=False)
    assert resp.status_code == 302
    user = db.get_user_by_email('bob@example.com')
    assert user['sub'] == 'sub-bob'


def test_callback_email_case_insensitive(oauth_env, monkeypatch):
    db.grant_user('Mixed@Example.com')
    _install_fake_oauth(monkeypatch, {
        'userinfo': {'sub': 'sub-mc', 'email': 'mixed@example.com', 'name': 'M'}
    })

    client = pwiki_app.app.test_client()
    resp = client.get('/auth/google/callback', follow_redirects=False)
    assert resp.status_code == 302


def test_logout_clears_session(oauth_env, monkeypatch):
    db.grant_user('lily@example.com')
    _install_fake_oauth(monkeypatch, {
        'userinfo': {'sub': 'sub-lily', 'email': 'lily@example.com', 'name': 'L'}
    })

    client = pwiki_app.app.test_client()
    client.get('/auth/google/callback', follow_redirects=False)
    with client.session_transaction() as sess:
        assert sess.get('username') == 'lily@example.com'

    resp = client.get('/auth/logout', follow_redirects=False)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert 'username' not in sess
        assert 'email' not in sess
        assert 'is_admin' not in sess


def test_login_uses_public_base_url_for_redirect_uri(oauth_env, monkeypatch):
    monkeypatch.setattr(config, 'PUBLIC_BASE_URL', 'https://example.com/newwiki')
    fake = _install_fake_oauth(monkeypatch, {})

    client = pwiki_app.app.test_client()
    resp = client.get('/auth/google/login', follow_redirects=False)
    assert resp.status_code == 302
    assert fake.google.last_redirect_uri == 'https://example.com/newwiki/auth/google/callback'


def test_oauth_next_open_redirect_blocked(oauth_env, monkeypatch):
    db.grant_user('mia@example.com')
    _install_fake_oauth(monkeypatch, {
        'userinfo': {'sub': 'sub-mia', 'email': 'mia@example.com', 'name': 'M'}
    })

    client = pwiki_app.app.test_client()
    # Try to set an absolute external next URL via the login flow.
    client.get('/auth/google/login?next=https://evil.example.com/x', follow_redirects=False)
    resp = client.get('/auth/google/callback', follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers.get('Location', '')
    assert 'evil.example.com' not in location
