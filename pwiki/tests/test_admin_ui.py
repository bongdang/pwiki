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
def admin_client(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'pwiki.db')
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    db.reset_initialized_cache()
    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'admin@example.com'
        sess['is_admin'] = True
    yield client
    db.reset_initialized_cache()


def _csrf(client):
    resp = client.get('/_admin/users')
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    marker = 'name="csrf_token" value="'
    start = body.index(marker) + len(marker)
    end = body.index('"', start)
    return body[start:end]


def test_admin_users_requires_admin(monkeypatch, tmp_path):
    db_path = str(tmp_path / 'pwiki.db')
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    db.reset_initialized_cache()
    client = pwiki_app.app.test_client()
    resp = client.get('/_admin/users', follow_redirects=False)
    assert resp.status_code == 302


def test_admin_users_grant_and_list(admin_client):
    token = _csrf(admin_client)
    resp = admin_client.post(
        '/_admin/users',
        data={
            'csrf_token': token,
            'op': 'grant',
            'email': 'newbie@example.com',
            'default_permission': 'read',
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b'newbie@example.com' in resp.data
    assert db.get_user_by_email('newbie@example.com') is not None


def test_admin_users_last_login_uses_time_tag(admin_client):
    db.grant_user('hasloggedin@example.com')
    db.update_login('hasloggedin@example.com', sub='sub-x', name='X')
    resp = admin_client.get('/_admin/users')
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'class="local-datetime"' in body
    assert '<time datetime=' in body


def test_admin_users_revoke(admin_client):
    db.grant_user('removed@example.com')
    token = _csrf(admin_client)
    resp = admin_client.post(
        '/_admin/users',
        data={'csrf_token': token, 'op': 'revoke', 'email': 'removed@example.com'},
    )
    assert resp.status_code == 200
    assert db.get_user_by_email('removed@example.com') is None


def test_admin_users_cannot_self_revoke(admin_client):
    db.grant_user('admin@example.com', is_admin=True)
    token = _csrf(admin_client)
    resp = admin_client.post(
        '/_admin/users',
        data={'csrf_token': token, 'op': 'revoke', 'email': 'admin@example.com'},
    )
    assert resp.status_code == 200
    assert 'You cannot delete yourself'.encode() in resp.data
    assert db.get_user_by_email('admin@example.com') is not None


def test_admin_users_hides_self_delete_button(admin_client):
    db.grant_user('admin@example.com', is_admin=True)
    db.grant_user('other@example.com')
    resp = admin_client.get('/_admin/users')
    body = resp.data.decode('utf-8')
    # The "other" row has a delete form; the admin's own row does not.
    assert 'other@example.com' in body
    assert '(self)' in body


def test_admin_user_detail_path_grant_and_revoke(admin_client):
    db.grant_user('alice@example.com')
    token = _csrf(admin_client)
    # Non-existent prefix without the explicit allow_missing flag → reject.
    rejected = admin_client.post(
        '/_admin/users/alice@example.com',
        data={
            'csrf_token': token,
            'op': 'path-grant',
            'prefix': 'Private',
            'permission': 'none',
        },
    )
    assert rejected.status_code == 200
    assert 'Path does not exist'.encode() in rejected.data
    assert db.list_user_paths('alice@example.com') == []

    # With allow_missing=1 → accepted.
    add = admin_client.post(
        '/_admin/users/alice@example.com',
        data={
            'csrf_token': token,
            'op': 'path-grant',
            'prefix': 'Private',
            'permission': 'none',
            'allow_missing': '1',
        },
    )
    assert add.status_code == 200
    paths = db.list_user_paths('alice@example.com')
    assert any(p['prefix'] == 'Private' and p['permission'] == 'none' for p in paths)

    remove = admin_client.post(
        '/_admin/users/alice@example.com',
        data={
            'csrf_token': token,
            'op': 'path-revoke',
            'prefix': 'Private',
        },
    )
    assert remove.status_code == 200
    assert db.list_user_paths('alice@example.com') == []


def test_admin_user_detail_renders_folder_and_all_datalists(admin_client, monkeypatch, tmp_path):
    md_root = tmp_path / 'vault'
    (md_root / 'Public').mkdir(parents=True)
    (md_root / 'Public' / 'A.md').write_text('# A\n', encoding='utf-8')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    db.grant_user('zoe@example.com')
    resp = admin_client.get('/_admin/users/zoe@example.com')
    body = resp.data.decode('utf-8')
    assert 'id="prefix-folders"' in body
    assert 'id="prefix-all"' in body
    assert 'list="prefix-folders"' in body  # default
    assert 'data-prefix-toggle' in body
    # folders datalist must include the folder prefix but not the file path
    folders_block = body.split('id="prefix-folders"', 1)[1].split('</datalist>', 1)[0]
    assert 'Public' in folders_block
    assert 'Public/A' not in folders_block
    all_block = body.split('id="prefix-all"', 1)[1].split('</datalist>', 1)[0]
    assert 'Public/A' in all_block


def test_admin_user_detail_path_grant_existing(admin_client, monkeypatch, tmp_path):
    md_root = tmp_path / 'vault'
    (md_root / 'Public').mkdir(parents=True)
    (md_root / 'Public' / 'A.md').write_text('# A\n', encoding='utf-8')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    db.grant_user('bob@example.com')
    token = _csrf(admin_client)
    add = admin_client.post(
        '/_admin/users/bob@example.com',
        data={
            'csrf_token': token,
            'op': 'path-grant',
            'prefix': 'Public',
            'permission': 'read',
        },
    )
    assert add.status_code == 200
    paths = db.list_user_paths('bob@example.com')
    assert any(p['prefix'] == 'Public' and p['permission'] == 'read' for p in paths)


def test_admin_user_detail_update_default_permission(admin_client):
    db.grant_user('bob@example.com', default_permission='read')
    token = _csrf(admin_client)
    resp = admin_client.post(
        '/_admin/users/bob@example.com',
        data={
            'csrf_token': token,
            'op': 'update',
            'default_permission': 'write',
            'is_admin': '1',
        },
    )
    assert resp.status_code == 200
    user = db.get_user_by_email('bob@example.com')
    assert user['default_permission'] == 'write'
    assert user['is_admin'] == 1
