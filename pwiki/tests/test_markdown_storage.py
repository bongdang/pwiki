import os
import subprocess
import sys

import pytest

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

import app as pwiki_app
import config
import storage


@pytest.fixture(autouse=True)
def _admin_test_client(monkeypatch):
    """Start this file's test clients with an admin session.

    After C8, all page routes must pass OAuth permission checks. conftest.py's
    `_default_admin` grants `tester` as an admin in the DB.
    """
    original = pwiki_app.app.test_client

    def factory(*args, **kwargs):
        client = original(*args, **kwargs)
        with client.session_transaction() as sess:
            sess.setdefault('username', 'tester')
            sess.setdefault('email', 'tester')
            sess.setdefault('is_admin', True)
            sess.setdefault('csrf_token', 'token')
        return client

    monkeypatch.setattr(pwiki_app.app, 'test_client', factory)


def test_markdown_storage_file_mapping(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))

    assert pwiki_app.get_storage_page_file('Folder/Page').endswith('Folder/Page.md')


def test_url_prefix_routes_serve_pages_and_static(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(tmp_path))
    monkeypatch.setattr(config, 'URL_PREFIX', '/newwiki')
    # Flask 3 blocks add_url_rule after the first request; we are intentionally
    # mutating routes for this test only.
    monkeypatch.setattr(pwiki_app.app, '_got_first_request', False, raising=False)
    pwiki_app._register_url_prefix_routes()
    pwiki_app.write_page('Note', '# Prefixed\n')

    client = pwiki_app.app.test_client()

    page = client.get('/newwiki/Note')
    assert page.status_code == 200
    assert b'<h1>Prefixed</h1>' in page.data

    static = client.get('/newwiki/static/style.css')
    assert static.status_code == 200
    assert static.mimetype == 'text/css'


def test_markdown_storage_read_write_does_not_create_keep(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))

    timestamp = pwiki_app.write_page('Notes/Alpha', '# Alpha\n\nhello')
    exists, read_timestamp, text = pwiki_app.read_page('Notes/Alpha')

    assert timestamp > 0
    assert exists is True
    assert read_timestamp == timestamp
    assert text == '# Alpha\n\nhello'


def test_write_string_to_file_keeps_original_when_replace_fails(monkeypatch, tmp_path):
    note = tmp_path / 'Note.md'
    note.write_text('original', encoding='utf-8')
    replace_sources = []

    def fail_replace(src, dst):
        replace_sources.append(src)
        raise OSError('replace failed')

    monkeypatch.setattr(pwiki_app.os, 'replace', fail_replace)

    with pytest.raises(OSError):
        pwiki_app.write_string_to_file(str(note), 'new text')

    assert note.read_text(encoding='utf-8') == 'original'
    assert replace_sources
    assert not os.path.exists(replace_sources[0])


def test_markdown_storage_browse_and_save(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Note', '# Title\n\nold')

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'tester'
        sess['csrf_token'] = 'token'

    browse = client.get('/Note')
    assert browse.status_code == 200
    assert b'<h1>Title</h1>' in browse.data

    _, old_time, old_text = pwiki_app.read_page('Note')
    saved = client.post(
        '/Note',
        data={
            'action': 'save',
            'oldtime': str(old_time),
            'oldhash': pwiki_app.page_content_hash(old_text),
            'csrf_token': 'token',
            'section': '0',
            'text': '# Title\n\nnew',
        },
    )
    assert saved.status_code == 302
    assert (md_root / 'Note.md').read_text(encoding='utf-8') == '# Title\n\nnew'


def test_markdown_storage_auto_commit_keeps_save_when_push_fails(monkeypatch, tmp_path):
    md_root = tmp_path / 'repo'
    md_root.mkdir()
    subprocess.run(['git', 'init'], cwd=md_root, check=True, capture_output=True)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'GIT_ROOT', str(md_root))
    monkeypatch.setattr(config, 'GIT_AUTO_COMMIT', True)
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Note', '# Title\n\nold')

    client = pwiki_app.app.test_client()
    _, _, old_text = pwiki_app.read_page('Note')
    saved = client.post(
        '/Note',
        data={
            'action': 'save',
            'oldhash': pwiki_app.page_content_hash(old_text),
            'csrf_token': 'token',
            'section': '0',
            'text': '# Title\n\nnew',
        },
        follow_redirects=True,
    )

    assert saved.status_code == 200
    assert (md_root / 'Note.md').read_text(encoding='utf-8') == '# Title\n\nnew'
    assert b'Git auto-commit/push failed' in saved.data

    log = subprocess.run(
        ['git', '-C', str(md_root), 'log', '--oneline', '--', 'Note.md'],
        check=True,
        capture_output=True,
        text=True,
    )
    assert 'Update Note.md via pwiki' in log.stdout


def test_markdown_storage_unchanged_save_skips_write_and_git(monkeypatch, tmp_path):
    md_root = tmp_path / 'repo'
    md_root.mkdir()
    subprocess.run(['git', 'init'], cwd=md_root, check=True, capture_output=True)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'GIT_ROOT', str(md_root))
    monkeypatch.setattr(config, 'GIT_AUTO_COMMIT', True)
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Note', '# Title\n\nold')
    subprocess.run(['git', '-C', str(md_root), 'add', 'Note.md'], check=True, capture_output=True)
    subprocess.run(
        [
            'git', '-C', str(md_root),
            '-c', 'user.name=tester',
            '-c', 'user.email=tester@example.com',
            'commit', '-m', 'Initial',
        ],
        check=True,
        capture_output=True,
    )
    before_mtime = (md_root / 'Note.md').stat().st_mtime_ns

    client = pwiki_app.app.test_client()
    _, _, old_text = pwiki_app.read_page('Note')
    saved = client.post(
        '/Note',
        data={
            'action': 'save',
            'oldhash': pwiki_app.page_content_hash(old_text),
            'csrf_token': 'token',
            'section': '0',
            'text': old_text,
        },
        follow_redirects=True,
    )

    assert saved.status_code == 200
    assert b'Git auto-commit/push failed' not in saved.data
    assert (md_root / 'Note.md').stat().st_mtime_ns == before_mtime
    log_count = subprocess.run(
        ['git', '-C', str(md_root), 'rev-list', '--count', 'HEAD'],
        check=True,
        capture_output=True,
        text=True,
    )
    assert log_count.stdout.strip() == '1'


def test_markdown_storage_blocks_save_during_merge(monkeypatch, tmp_path):
    md_root = tmp_path / 'repo'
    md_root.mkdir()
    subprocess.run(['git', 'init'], cwd=md_root, check=True, capture_output=True)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'GIT_ROOT', str(md_root))
    monkeypatch.setattr(config, 'GIT_AUTO_COMMIT', False)
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Note', '# Title\n\nold')
    (md_root / '.git' / 'MERGE_HEAD').write_text('0' * 40 + '\n', encoding='utf-8')

    client = pwiki_app.app.test_client()
    _, _, old_text = pwiki_app.read_page('Note')
    saved = client.post(
        '/Note',
        data={
            'action': 'save',
            'oldhash': pwiki_app.page_content_hash(old_text),
            'csrf_token': 'token',
            'section': '0',
            'text': '# Title\n\nnew',
        },
    )

    assert saved.status_code == 409
    assert b'Git state blocks this save' in saved.data
    assert (md_root / 'Note.md').read_text(encoding='utf-8') == '# Title\n\nold'


def test_markdown_storage_creates_new_page_without_conflict(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'tester'
        sess['csrf_token'] = 'token'

    saved = client.post(
        '/NewPage',
        data={
            'action': 'save',
            'oldtime': '0',
            'oldhash': '',
            'csrf_token': 'token',
            'section': '0',
            'text': '# New\n',
        },
    )

    assert saved.status_code == 302
    assert (md_root / 'NewPage.md').read_text(encoding='utf-8') == '# New\n'


def test_addpage_prefills_current_folder(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Folder/Current', '# Current')

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'tester'

    response = client.get('/Folder/Current?action=addpage')

    assert response.status_code == 200
    assert b'value="Folder/"' in response.data


def test_markdown_storage_rejects_invalid_filename_chars(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'tester'
        sess['csrf_token'] = 'token'

    saved = client.post(
        '/Bad:Name',
        data={
            'action': 'save',
            'oldtime': '0',
            'oldhash': '',
            'csrf_token': 'token',
            'section': '0',
            'text': '# Bad\n',
        },
    )

    assert saved.status_code == 400
    assert b'Invalid page id.' in saved.data
    assert not (md_root / 'Bad:Name.md').exists()


def test_save_handles_filesystem_write_error(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Note', '# Title\n\nold')

    def fail_write(*args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(storage, 'write_string_to_file', fail_write)

    client = pwiki_app.app.test_client()
    _, _, old_text = pwiki_app.read_page('Note')
    saved = client.post(
        '/Note',
        data={
            'action': 'save',
            'oldhash': pwiki_app.page_content_hash(old_text),
            'csrf_token': 'token',
            'section': '0',
            'text': '# Title\n\nnew',
        },
    )

    assert saved.status_code == 500
    assert 'Could not save this page'.encode('utf-8') in saved.data
    assert (md_root / 'Note.md').read_text(encoding='utf-8') == '# Title\n\nold'


def test_markdown_storage_search_and_delete(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Notes/Alpha', '# Alpha\n\nneedle text')
    pwiki_app.write_page('Beta', '# Beta\n\nother')

    client = pwiki_app.app.test_client()

    search = client.get('/?action=search&q=needle')
    assert search.status_code == 200
    assert b'Notes/Alpha' in search.data
    assert b'needle text' in search.data

    history = client.get('/Notes/Alpha?action=history')
    assert history.status_code == 400
    assert b'Unknown action' in history.data

    with client.session_transaction() as sess:
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['csrf_token'] = 'token'

    delete_page = client.get('/Notes/Alpha?action=delete')
    assert delete_page.status_code == 200
    assert b'History is expected to come from Git' in delete_page.data

    deleted = client.post(
        '/Notes/Alpha',
        data={'action': 'delete', 'csrf_token': 'token'},
    )
    assert deleted.status_code == 302
    assert not (md_root / 'Notes' / 'Alpha.md').exists()


def test_markdown_storage_auto_commit_after_delete(monkeypatch, tmp_path):
    md_root = tmp_path / 'repo'
    md_root.mkdir()
    subprocess.run(['git', 'init'], cwd=md_root, check=True, capture_output=True)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'GIT_ROOT', str(md_root))
    monkeypatch.setattr(config, 'GIT_AUTO_COMMIT', True)
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Note', '# Title\n')
    subprocess.run(['git', '-C', str(md_root), 'add', 'Note.md'], check=True, capture_output=True)
    subprocess.run(
        [
            'git', '-C', str(md_root),
            '-c', 'user.name=tester',
            '-c', 'user.email=tester@example.com',
            'commit', '-m', 'Initial',
        ],
        check=True,
        capture_output=True,
    )

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['csrf_token'] = 'token'

    deleted = client.post('/Note', data={'action': 'delete', 'csrf_token': 'token'})

    assert deleted.status_code == 302
    assert not (md_root / 'Note.md').exists()

    # The removal must be committed so the deletion propagates and the working
    # tree is left clean (a dirty tree would block the host's pull --ff-only).
    status = subprocess.run(
        ['git', '-C', str(md_root), 'status', '--porcelain'],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ''
    log = subprocess.run(
        ['git', '-C', str(md_root), 'log', '--oneline', '--', 'Note.md'],
        check=True,
        capture_output=True,
        text=True,
    )
    assert 'Update Note.md via pwiki' in log.stdout


def test_markdown_storage_blocks_delete_during_merge(monkeypatch, tmp_path):
    md_root = tmp_path / 'repo'
    md_root.mkdir()
    subprocess.run(['git', 'init'], cwd=md_root, check=True, capture_output=True)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'GIT_ROOT', str(md_root))
    monkeypatch.setattr(config, 'GIT_AUTO_COMMIT', False)
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Note', '# Title\n')
    (md_root / '.git' / 'MERGE_HEAD').write_text('0' * 40 + '\n', encoding='utf-8')

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['csrf_token'] = 'token'

    deleted = client.post('/Note', data={'action': 'delete', 'csrf_token': 'token'})

    assert deleted.status_code == 409
    assert (md_root / 'Note.md').exists()


def test_delete_handles_filesystem_remove_error(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Note', '# Title\n')

    def fail_remove(path):
        raise OSError('permission denied')

    monkeypatch.setattr(pwiki_app.os, 'remove', fail_remove)

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['csrf_token'] = 'token'

    deleted = client.post('/Note', data={'action': 'delete', 'csrf_token': 'token'})

    assert deleted.status_code == 500
    assert 'Could not delete this page'.encode('utf-8') in deleted.data
    assert (md_root / 'Note.md').exists()


def test_markdown_storage_attachment_route(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    asset = md_root / 'assets' / 'pic.png'
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b'png-bytes')
    (md_root / 'assets' / 'secret.exe').write_bytes(b'nope')
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))

    client = pwiki_app.app.test_client()
    ok = client.get('/attach/assets/pic.png')
    assert ok.status_code == 200
    assert ok.data == b'png-bytes'

    blocked_ext = client.get('/attach/assets/secret.exe')
    assert blocked_ext.status_code == 404

    traversal = client.get('/attach/../secret.txt')
    assert traversal.status_code == 404


def test_upload_file_route_validates_path_and_extension(monkeypatch, tmp_path):
    upload_root = tmp_path / 'upload'
    asset = upload_root / 'assets' / 'pic.png'
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b'png-bytes')
    (upload_root / 'assets' / 'secret.exe').write_bytes(b'nope')
    monkeypatch.setattr(config, 'UPLOAD_DIR', str(upload_root))

    client = pwiki_app.app.test_client()
    ok = client.get('/upload_files/assets/pic.png')
    assert ok.status_code == 200
    assert ok.data == b'png-bytes'

    blocked_ext = client.get('/upload_files/assets/secret.exe')
    assert blocked_ext.status_code == 404

    traversal = client.get('/upload_files/../secret.txt')
    assert traversal.status_code == 404


def test_markdown_storage_preserves_space_in_page_route(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    pwiki_app.write_page('Folder/Page With Space', '# Space Page')

    client = pwiki_app.app.test_client()
    response = client.get('/Folder/Page%20With%20Space')

    assert response.status_code == 200
    assert b'<h1>Space Page</h1>' in response.data


def test_read_only_blocks_write_actions(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', True)
    pwiki_app.write_page('ReadOnly', '# Read only')

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['csrf_token'] = 'token'

    assert client.get('/ReadOnly?action=edit').status_code == 403
    assert client.get('/ReadOnly?action=delete').status_code == 403
    assert client.get('/?action=addpage').status_code == 403

    saved = client.post(
        '/ReadOnly',
        data={
            'action': 'save',
            'oldtime': '0',
            'oldhash': '',
            'csrf_token': 'token',
            'section': '0',
            'text': '# Changed',
        },
    )
    assert saved.status_code == 403
    assert (md_root / 'ReadOnly.md').read_text(encoding='utf-8') == '# Read only'


def test_markdown_storage_detects_hash_conflict(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Conflict', '# Title\n\noriginal')
    _, old_time, old_text = pwiki_app.read_page('Conflict')
    pwiki_app.write_page('Conflict', '# Title\n\nexternal edit')

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'tester'
        sess['csrf_token'] = 'token'

    response = client.post(
        '/Conflict',
        data={
            'action': 'save',
            'oldtime': str(old_time),
            'oldhash': pwiki_app.page_content_hash(old_text),
            'csrf_token': 'token',
            'section': '0',
            'text': '# Title\n\nmy edit',
        },
    )

    assert response.status_code == 200
    assert b'Someone saved this page after you started editing.' in response.data
    assert (md_root / 'Conflict.md').read_text(encoding='utf-8') == '# Title\n\nexternal edit'


def test_markdown_storage_rechecks_hash_immediately_before_write(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    pwiki_app.write_page('Race', '# Title\n\noriginal')
    _, old_time, old_text = pwiki_app.read_page('Race')

    def external_edit_during_save():
        pwiki_app.write_page('Race', '# Title\n\nexternal edit')
        return None

    monkeypatch.setattr('routes.pages._blocking_git_write_state', external_edit_during_save)

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'tester'
        sess['csrf_token'] = 'token'

    response = client.post(
        '/Race',
        data={
            'action': 'save',
            'oldtime': str(old_time),
            'oldhash': pwiki_app.page_content_hash(old_text),
            'csrf_token': 'token',
            'section': '0',
            'text': '# Title\n\nmy edit',
        },
    )

    assert response.status_code == 200
    assert b'Someone saved this page after you started editing.' in response.data
    assert (md_root / 'Race.md').read_text(encoding='utf-8') == '# Title\n\nexternal edit'


def test_markdown_storage_preserves_existing_crlf_newlines(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    note = md_root / 'CRLF.md'
    note.parent.mkdir(parents=True)
    note.write_bytes(b'# Title\r\n\r\nold\r\n')
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    _, old_time, old_text = pwiki_app.read_page('CRLF')

    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'tester'
        sess['csrf_token'] = 'token'

    saved = client.post(
        '/CRLF',
        data={
            'action': 'save',
            'oldtime': str(old_time),
            'oldhash': pwiki_app.page_content_hash(old_text),
            'csrf_token': 'token',
            'section': '0',
            'text': '# Title\n\nnew\n',
        },
    )

    assert saved.status_code == 302
    assert note.read_bytes() == b'# Title\r\n\r\nnew\r\n'


def test_markdown_storage_preserves_mixed_newline_pattern(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    note = md_root / 'Mixed.md'
    note.parent.mkdir(parents=True)
    note.write_bytes(b'a\r\nb\nc\r')
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))

    pwiki_app.write_page('Mixed', 'x\ny\nz\n')

    assert note.read_bytes() == b'x\r\ny\nz\r'


def test_apply_newline_style_preserves_existing_crlf_sequence():
    result = pwiki_app._apply_newline_style(
        'x\ny\nz\n',
        ['\r\n', '\r\n', '\r\n'],
    )

    assert result == 'x\r\ny\r\nz\r\n'


def test_apply_newline_style_uses_dominant_existing_newline_for_extra_lines():
    result = pwiki_app._apply_newline_style(
        'x\ny\nz\nextra\n',
        ['\n', '\r\n', '\n'],
    )

    assert result == 'x\ny\r\nz\nextra\n'


def test_read_only_hides_create_link_for_missing_page(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', True)

    client = pwiki_app.app.test_client()
    response = client.get('/NotThere')

    assert response.status_code == 200
    assert b'does not exist' in response.data
    assert b'action=edit' not in response.data


def test_writable_mode_shows_create_link_for_missing_page(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)

    client = pwiki_app.app.test_client()
    response = client.get('/NotThere')

    assert response.status_code == 200
    assert b'Create this page' in response.data


def test_read_only_startup_does_not_create_home_page(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'HOME_PAGE', 'MissingHome')
    monkeypatch.setattr(config, 'READ_ONLY', True)

    pwiki_app._startup()

    assert not (md_root / 'MissingHome.md').exists()


def test_markdown_storage_sidebar_tree(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'STORAGE_BACKEND', 'markdown')
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    pwiki_app.write_page('Folder/Alpha', '# Alpha')
    pwiki_app.write_page('Beta', '# Beta')

    client = pwiki_app.app.test_client()
    response = client.get('/')

    assert response.status_code == 200
    assert b'sidebar-tree' in response.data
    assert b'Folder' in response.data
    assert b'Alpha' in response.data
    assert b'Beta' in response.data
    assert b'href="/Folder/Alpha"' in response.data


def test_section_edit_get_returns_only_target_section(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    body = "# Top\nintro\n## One\nalpha\n## Two\nbeta\n"
    pwiki_app.write_page('Sec', body)

    client = pwiki_app.app.test_client()
    resp = client.get('/Sec?action=edit&section=2')
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert '## One' in body
    assert 'alpha' in body
    assert '## Two' not in body
    assert '# Top' not in body


def test_section_save_replaces_only_target_section(monkeypatch, tmp_path):
    md_root = tmp_path / 'markdown'
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    original = "# Top\nintro\n## One\nalpha\n## Two\nbeta\n"
    pwiki_app.write_page('Sec', original)
    _, _, old_text = pwiki_app.read_page('Sec')

    client = pwiki_app.app.test_client()
    resp = client.post(
        '/Sec',
        data={
            'action': 'save',
            'csrf_token': 'token',
            'section': '2',
            'text': '## One\nNEW alpha\n',
            'oldhash': pwiki_app.page_content_hash(old_text),
        },
    )
    assert resp.status_code in (200, 302)
    saved = (md_root / 'Sec.md').read_text(encoding='utf-8')
    assert 'NEW alpha' in saved
    assert '# Top' in saved  # preamble preserved
    assert '## Two' in saved  # later section preserved
    assert 'beta' in saved
