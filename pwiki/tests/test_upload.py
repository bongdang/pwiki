import io
import os
import subprocess
import sys
import unicodedata

import pytest

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

import app as pwiki_app
import config


@pytest.fixture(autouse=True)
def _admin_test_client(monkeypatch):
    """Start test clients as an authenticated admin (mirrors conftest grant)."""
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


def _png_bytes() -> bytes:
    # A 1x1 PNG is enough; content is opaque to the upload route.
    return (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
        b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    )


def _writable_vault(monkeypatch, tmp_path):
    md_root = tmp_path / 'vault'
    md_root.mkdir()
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'READ_ONLY', False)
    monkeypatch.setattr(config, 'ATTACHMENT_SUBDIR', 'attachments')
    return md_root


def _upload(client, *, filename='photo.png', data=None, csrf='token', page='Note'):
    fields = {
        'action': 'upload',
        'file': (io.BytesIO(data if data is not None else _png_bytes()), filename),
    }
    if csrf is not None:
        fields['csrf_token'] = csrf
    return client.post(
        f'/{page}',
        data=fields,
        content_type='multipart/form-data',
    )


def test_upload_saves_attachment_and_returns_embed(monkeypatch, tmp_path):
    md_root = _writable_vault(monkeypatch, tmp_path)
    client = pwiki_app.app.test_client()

    resp = _upload(client, filename='photo.png')

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['ok'] is True
    assert payload['path'] == 'attachments/photo.png'
    assert payload['embed'] == '![[attachments/photo.png]]'
    assert (md_root / 'attachments' / 'photo.png').exists()


def test_upload_rejects_disallowed_extension(monkeypatch, tmp_path):
    md_root = _writable_vault(monkeypatch, tmp_path)
    client = pwiki_app.app.test_client()

    for bad in ('evil.svg', 'evil.exe'):
        resp = _upload(client, filename=bad)
        assert resp.status_code == 400
        assert resp.get_json()['ok'] is False
    assert not (md_root / 'attachments').exists() or not any((md_root / 'attachments').iterdir())


def test_upload_blocked_when_read_only(monkeypatch, tmp_path):
    _writable_vault(monkeypatch, tmp_path)
    monkeypatch.setattr(config, 'READ_ONLY', True)
    client = pwiki_app.app.test_client()

    resp = _upload(client)

    assert resp.status_code == 403
    assert resp.get_json()['ok'] is False


def test_upload_requires_write_permission(monkeypatch, tmp_path):
    _writable_vault(monkeypatch, tmp_path)
    client = pwiki_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'nobody@example.com'
        sess['email'] = 'nobody@example.com'
        sess['is_admin'] = False

    resp = _upload(client)

    assert resp.status_code == 403
    assert resp.get_json()['ok'] is False


def test_upload_rejects_without_csrf(monkeypatch, tmp_path):
    _writable_vault(monkeypatch, tmp_path)
    client = pwiki_app.app.test_client()

    resp = _upload(client, csrf=None)

    assert resp.status_code == 403
    assert resp.get_json()['ok'] is False


def test_upload_sanitizes_traversal_filename(monkeypatch, tmp_path):
    md_root = _writable_vault(monkeypatch, tmp_path)
    client = pwiki_app.app.test_client()

    resp = _upload(client, filename='../../evil.png')

    assert resp.status_code == 200
    assert resp.get_json()['path'] == 'attachments/evil.png'
    assert (md_root / 'attachments' / 'evil.png').exists()
    # Nothing escaped the vault.
    assert not (tmp_path / 'evil.png').exists()


def test_upload_preserves_unicode_filename(monkeypatch, tmp_path):
    md_root = _writable_vault(monkeypatch, tmp_path)
    client = pwiki_app.app.test_client()

    name = unicodedata.normalize('NFC', '한글 사진.png')
    resp = _upload(client, filename=name)

    assert resp.status_code == 200
    assert resp.get_json()['path'] == f'attachments/{name}'
    assert (md_root / 'attachments' / name).exists()


def test_upload_sanitizes_wikilink_special_chars(monkeypatch, tmp_path):
    md_root = _writable_vault(monkeypatch, tmp_path)
    client = pwiki_app.app.test_client()

    # `#`, `[`, `]`, `^` are special inside `![[...]]`; they must be neutralized
    # so the inserted embed renders as an image instead of breaking.
    resp = _upload(client, filename='a#b[1]^c.png')

    assert resp.status_code == 200
    safe = resp.get_json()['path']
    assert safe == 'attachments/a_b_1__c.png'
    assert (md_root / 'attachments' / 'a_b_1__c.png').exists()
    html = pwiki_app.markdown_to_html('![[' + safe + ']]', 'P')
    assert '<img' in html and 'a_b_1__c.png' in html


def test_upload_collision_appends_suffix(monkeypatch, tmp_path):
    md_root = _writable_vault(monkeypatch, tmp_path)
    client = pwiki_app.app.test_client()

    first = _upload(client, filename='dup.png')
    second = _upload(client, filename='dup.png')

    assert first.get_json()['path'] == 'attachments/dup.png'
    assert second.get_json()['path'] == 'attachments/dup-1.png'
    assert (md_root / 'attachments' / 'dup.png').exists()
    assert (md_root / 'attachments' / 'dup-1.png').exists()


def test_upload_auto_commits_into_vault(monkeypatch, tmp_path):
    md_root = tmp_path / 'repo'
    md_root.mkdir()
    subprocess.run(['git', 'init'], cwd=md_root, check=True, capture_output=True)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'GIT_ROOT', str(md_root))
    monkeypatch.setattr(config, 'GIT_AUTO_COMMIT', True)
    monkeypatch.setattr(config, 'READ_ONLY', False)
    monkeypatch.setattr(config, 'ATTACHMENT_SUBDIR', 'attachments')

    client = pwiki_app.app.test_client()
    resp = _upload(client, filename='shot.png')

    assert resp.status_code == 200
    assert (md_root / 'attachments' / 'shot.png').exists()

    # The new attachment must be committed so it can sync; no remote means push
    # fails, but the local commit is kept and the working tree is left clean.
    status = subprocess.run(
        ['git', '-C', str(md_root), 'status', '--porcelain'],
        check=True, capture_output=True, text=True,
    )
    assert status.stdout.strip() == ''
    log = subprocess.run(
        ['git', '-C', str(md_root), 'log', '--oneline', '--', 'attachments/shot.png'],
        check=True, capture_output=True, text=True,
    )
    assert 'attachments/shot.png via pwiki' in log.stdout


def test_upload_blocked_during_merge(monkeypatch, tmp_path):
    md_root = tmp_path / 'repo'
    md_root.mkdir()
    subprocess.run(['git', 'init'], cwd=md_root, check=True, capture_output=True)
    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'GIT_ROOT', str(md_root))
    monkeypatch.setattr(config, 'GIT_AUTO_COMMIT', False)
    monkeypatch.setattr(config, 'READ_ONLY', False)
    (md_root / '.git' / 'MERGE_HEAD').write_text('0' * 40 + '\n', encoding='utf-8')

    client = pwiki_app.app.test_client()
    resp = _upload(client, filename='shot.png')

    assert resp.status_code == 409
    assert not (md_root / 'attachments' / 'shot.png').exists()


def test_upload_too_large_returns_json_413(monkeypatch, tmp_path):
    _writable_vault(monkeypatch, tmp_path)
    monkeypatch.setitem(pwiki_app.app.config, 'MAX_CONTENT_LENGTH', 8)
    monkeypatch.setattr(config, 'MAX_CONTENT_LENGTH', 8)
    client = pwiki_app.app.test_client()

    resp = client.post(
        '/Note',
        data={
            'action': 'upload',
            'csrf_token': 'token',
            'file': (io.BytesIO(b'x' * 4096), 'big.png'),
        },
        content_type='multipart/form-data',
        headers={'Accept': 'application/json'},
    )

    assert resp.status_code == 413
    assert resp.get_json()['ok'] is False
