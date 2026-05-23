"""Tests for the internal read-only API (token + CIDR + read-only guarantees)."""

import os
import sys

import pytest

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

import config
import app as pwiki_app


TOKEN = 'test-internal-token-abc123'


@pytest.fixture
def api_env(monkeypatch, tmp_path):
    md_root = tmp_path / 'vault'
    md_root.mkdir()
    (md_root / 'index.md').write_text('# Home\nWelcome to pwiki.\n', encoding='utf-8')
    (md_root / 'Projects').mkdir()
    (md_root / 'Projects' / 'pwiki.md').write_text(
        '# pwiki\nUses Google OAuth for auth.\n',
        encoding='utf-8',
    )
    (md_root / 'Projects' / 'todo.md').write_text(
        '# TODO\nAdd more tests.\n',
        encoding='utf-8',
    )
    (md_root / 'Projects' / 'Archive').mkdir()
    (md_root / 'Projects' / 'Archive' / 'old.md').write_text('# old\n', encoding='utf-8')
    # Hidden dir + file: must be skipped.
    (md_root / '.git').mkdir()
    (md_root / '.git' / 'config').write_text('x', encoding='utf-8')
    (md_root / '.hidden.md').write_text('# secret\n', encoding='utf-8')
    # Non-markdown file: must be excluded from folder listings and search.
    (md_root / 'image.png').write_bytes(b'fake-png-bytes')

    monkeypatch.setattr(config, 'MARKDOWN_DIR', str(md_root))
    monkeypatch.setattr(config, 'INTERNAL_API_TOKEN', TOKEN)
    monkeypatch.setattr(config, 'INTERNAL_API_ALLOWED_CIDRS', '127.0.0.1/32,::1/128')
    monkeypatch.setattr(config, 'INTERNAL_API_TRUSTED_PROXY_CIDRS', '')
    return md_root


def _auth(token=TOKEN):
    return {'Authorization': f'Bearer {token}'}


# ---------------------------------------------------------------------------
# Activation / authentication / network guards
# ---------------------------------------------------------------------------

def test_disabled_when_token_unset(api_env, monkeypatch):
    monkeypatch.setattr(config, 'INTERNAL_API_TOKEN', '')
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/health', headers=_auth())
    assert resp.status_code == 404
    assert resp.get_json()['error']['code'] == 'not_configured'


def test_missing_token_returns_401(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/health')
    assert resp.status_code == 401
    assert resp.get_json()['error']['code'] == 'unauthorized'


def test_invalid_token_returns_401(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/health', headers=_auth('wrong-token'))
    assert resp.status_code == 401


def test_malformed_authorization_header_returns_401(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get(
        '/api/internal/health',
        headers={'Authorization': TOKEN},  # missing "Bearer " prefix
    )
    assert resp.status_code == 401


def test_request_outside_allowed_cidr_returns_403(api_env, monkeypatch):
    monkeypatch.setattr(config, 'INTERNAL_API_ALLOWED_CIDRS', '10.0.0.0/8')
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/health', headers=_auth())
    assert resp.status_code == 403
    assert resp.get_json()['error']['code'] == 'forbidden_cidr'


def test_xff_ignored_without_trusted_proxy_configuration(api_env, monkeypatch):
    # 127.0.0.1 is NOT in the allowlist; XFF claims 10.0.0.5 (which IS in the
    # allowlist) but without trusted-proxy CIDRs configured we must NOT honor
    # that header — the request still has to be denied.
    monkeypatch.setattr(config, 'INTERNAL_API_ALLOWED_CIDRS', '10.0.0.0/8')
    monkeypatch.setattr(config, 'INTERNAL_API_TRUSTED_PROXY_CIDRS', '')
    client = pwiki_app.app.test_client()
    resp = client.get(
        '/api/internal/health',
        headers={**_auth(), 'X-Forwarded-For': '10.0.0.5'},
    )
    assert resp.status_code == 403


def test_xff_honored_when_remote_is_a_trusted_proxy(api_env, monkeypatch):
    monkeypatch.setattr(config, 'INTERNAL_API_ALLOWED_CIDRS', '10.0.0.0/8')
    monkeypatch.setattr(config, 'INTERNAL_API_TRUSTED_PROXY_CIDRS', '127.0.0.0/8')
    client = pwiki_app.app.test_client()
    resp = client.get(
        '/api/internal/health',
        headers={**_auth(), 'X-Forwarded-For': '10.1.2.3'},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Endpoint behavior
# ---------------------------------------------------------------------------

def test_health_returns_status(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/health', headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {'ok': True, 'service': 'pwiki', 'internalApi': True}


def test_search_finds_match_and_returns_snippet(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/search?q=oauth', headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['query'] == 'oauth'
    assert body['count'] >= 1
    hits = {hit['path']: hit for hit in body['results']}
    assert 'Projects/pwiki.md' in hits
    hit = hits['Projects/pwiki.md']
    assert 'oauth' in hit['snippet'].lower()
    assert hit['title'] == 'pwiki'
    assert hit['modifiedTime']


def test_search_requires_query(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/search', headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()['error']['code'] == 'bad_request'


def test_search_respects_limit(api_env):
    client = pwiki_app.app.test_client()
    # Every test page contains a heading, so '#' matches all of them.
    resp = client.get('/api/internal/search?q=%23&limit=2', headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['count'] == 2
    assert len(body['results']) == 2


def test_search_skips_hidden_files(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/search?q=secret', headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert all(not hit['path'].startswith('.') for hit in body['results'])


def test_page_returns_content(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get(
        '/api/internal/page?path=Projects/pwiki.md',
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['path'] == 'Projects/pwiki.md'
    assert body['title'] == 'pwiki'
    assert body['content'].startswith('# pwiki')
    assert body['modifiedTime']


def test_page_requires_path(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/page', headers=_auth())
    assert resp.status_code == 400


def test_page_rejects_non_markdown_extension(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/page?path=image.png', headers=_auth())
    assert resp.status_code == 400


def test_page_not_found(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/page?path=Projects/missing.md', headers=_auth())
    assert resp.status_code == 404


def test_page_blocks_path_traversal(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/page?path=../outside.md', headers=_auth())
    assert resp.status_code == 403
    assert resp.get_json()['error']['code'] == 'forbidden_path'


def test_page_blocks_symlink_escape(api_env, tmp_path):
    outside = tmp_path / 'outside.md'
    outside.write_text('top secret', encoding='utf-8')
    link = api_env / 'leak.md'
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks not supported on this filesystem')
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/page?path=leak.md', headers=_auth())
    assert resp.status_code == 403


def test_folder_root_lists_top_level(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/folder', headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['path'] == ''
    paths = {item['path'] for item in body['items']}
    assert 'index.md' in paths
    assert 'Projects' in paths
    # Hidden dirs/files and non-markdown attachments are excluded by default.
    assert '.git' not in paths
    assert '.hidden.md' not in paths
    assert 'image.png' not in paths


def test_folder_nested_lists_children(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/folder?path=Projects', headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    paths = {item['path'] for item in body['items']}
    assert {'Projects/pwiki.md', 'Projects/todo.md', 'Projects/Archive'} <= paths


def test_folder_recursive_applies_limit(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get(
        '/api/internal/folder?path=Projects&recursive=true&limit=2',
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['count'] == 2
    assert len(body['items']) == 2


def test_folder_blocks_traversal(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/folder?path=../', headers=_auth())
    assert resp.status_code == 403


def test_folder_not_found(api_env):
    client = pwiki_app.app.test_client()
    resp = client.get('/api/internal/folder?path=Projects/Missing', headers=_auth())
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Write-method enforcement: only GET is exposed.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('method', ['PUT', 'PATCH', 'DELETE'])
def test_write_methods_are_not_exposed(api_env, method):
    # POST is intentionally not parameterized here: the wiki's catch-all
    # `/<path:page_id>` already responds to POST, so a POST to the internal
    # API URL is routed to that handler (and never reaches an API view). The
    # internal API itself only registers GET, which the assertions below
    # confirm via the view function's `methods` set.
    client = pwiki_app.app.test_client()
    resp = client.open(
        '/api/internal/page?path=Projects/pwiki.md',
        method=method,
        headers=_auth(),
    )
    assert resp.status_code == 405


def test_internal_api_routes_only_accept_get():
    for endpoint in (
        'internal_api_health',
        'internal_api_search',
        'internal_api_page',
        'internal_api_folder',
    ):
        rules = [
            rule for rule in pwiki_app.app.url_map.iter_rules()
            if rule.endpoint == endpoint
        ]
        assert rules, f'route {endpoint} not registered'
        for rule in rules:
            # HEAD/OPTIONS are auto-added by Flask; the only "data" method
            # we expose must be GET.
            data_methods = rule.methods - {'HEAD', 'OPTIONS'}
            assert data_methods == {'GET'}, f'{endpoint} allows {data_methods}'
