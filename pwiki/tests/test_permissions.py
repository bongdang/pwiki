import os
import sys

import pytest

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

import config
import db
import permissions


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Isolate each test with its own SQLite file."""
    db_path = str(tmp_path / 'pwiki.db')
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    db.reset_initialized_cache()
    yield db_path
    db.reset_initialized_cache()


def test_unknown_user_has_no_access(fresh_db):
    assert permissions.resolve_permission('nobody@example.com', 'hello') == 'none'
    assert permissions.resolve_permission(None, 'hello') == 'none'


def test_default_read_permission(fresh_db):
    db.grant_user('alice@example.com')
    assert permissions.resolve_permission('alice@example.com', 'anything') == 'read'
    assert permissions.has_permission('alice@example.com', 'anything', 'read')
    assert not permissions.has_permission('alice@example.com', 'anything', 'write')


def test_admin_bypass(fresh_db):
    db.grant_user('admin@example.com', is_admin=True, default_permission='write')
    assert permissions.resolve_permission('admin@example.com', 'secret/doc') == 'write'
    assert permissions.has_permission('admin@example.com', 'secret/doc', 'write')


def test_path_override_extends_permission(fresh_db):
    db.grant_user('bob@example.com', default_permission='read')
    db.upsert_user_path('bob@example.com', 'Public', 'write')
    assert permissions.resolve_permission('bob@example.com', 'Public/TeamA/Note') == 'write'
    assert permissions.resolve_permission('bob@example.com', 'Other/Memo') == 'read'


def test_path_override_revokes(fresh_db):
    db.grant_user('carol@example.com', default_permission='read')
    db.upsert_user_path('carol@example.com', 'Private', 'none')
    assert permissions.resolve_permission('carol@example.com', 'Private/Diary') == 'none'
    assert permissions.resolve_permission('carol@example.com', 'Public/Note') == 'read'


def test_most_specific_prefix_wins(fresh_db):
    db.grant_user('dave@example.com', default_permission='none')
    db.upsert_user_path('dave@example.com', 'Recordings', 'read')
    db.upsert_user_path('dave@example.com', 'Recordings/Private', 'none')
    db.upsert_user_path('dave@example.com', 'Recordings/Public', 'write')
    assert permissions.resolve_permission('dave@example.com', 'Recordings/Public/A') == 'write'
    assert permissions.resolve_permission('dave@example.com', 'Recordings/Private/B') == 'none'
    assert permissions.resolve_permission('dave@example.com', 'Recordings/Other/C') == 'read'
    assert permissions.resolve_permission('dave@example.com', 'Outside/D') == 'none'


def test_prefix_segment_boundary(fresh_db):
    """A prefix must not match a longer sibling segment."""
    db.grant_user('erin@example.com', default_permission='none')
    db.upsert_user_path('erin@example.com', 'Record', 'write')
    assert permissions.resolve_permission('erin@example.com', 'Record/A') == 'write'
    assert permissions.resolve_permission('erin@example.com', 'Record') == 'write'
    assert permissions.resolve_permission('erin@example.com', 'Recordings/A') == 'none'
    assert permissions.resolve_permission('erin@example.com', 'Recordings') == 'none'


def test_empty_prefix_matches_all(fresh_db):
    db.grant_user('frank@example.com', default_permission='none')
    db.upsert_user_path('frank@example.com', '', 'read')
    assert permissions.resolve_permission('frank@example.com', 'anything/here') == 'read'


def test_prefix_normalization(fresh_db):
    db.grant_user('gina@example.com', default_permission='none')
    db.upsert_user_path('gina@example.com', '/Public/TeamA/', 'write')
    assert permissions.resolve_permission('gina@example.com', 'Public/TeamA/Doc') == 'write'


def test_filter_visible_paths(fresh_db):
    db.grant_user('hank@example.com', default_permission='read')
    db.upsert_user_path('hank@example.com', 'Private', 'none')
    pages = ['Public/A', 'Private/B', 'Records/C', 'Private']
    visible = permissions.filter_visible_paths('hank@example.com', pages)
    assert visible == ['Public/A', 'Records/C']


def test_filter_visible_paths_admin(fresh_db):
    db.grant_user('iris@example.com', is_admin=True, default_permission='write')
    pages = ['a', 'b', 'c']
    assert permissions.filter_visible_paths('iris@example.com', pages) == pages


def test_revoke_user_cascades_paths(fresh_db):
    db.grant_user('jay@example.com')
    db.upsert_user_path('jay@example.com', 'Folder', 'write')
    assert db.revoke_user('jay@example.com')
    assert db.list_user_paths('jay@example.com') == []


def test_update_login_records_sub(fresh_db):
    db.grant_user('kate@example.com')
    db.update_login('kate@example.com', 'sub-12345', 'Kate')
    user = db.get_user_by_sub('sub-12345')
    assert user is not None
    assert user['email'] == 'kate@example.com'
    assert user['name'] == 'Kate'
    assert user['last_login_at']


def test_upsert_replaces_existing_override(fresh_db):
    db.grant_user('lex@example.com')
    db.upsert_user_path('lex@example.com', 'Folder', 'read')
    db.upsert_user_path('lex@example.com', 'Folder', 'write')
    paths = db.list_user_paths('lex@example.com')
    assert len(paths) == 1
    assert paths[0]['permission'] == 'write'
