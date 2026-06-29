import argparse
import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import install as installer


def _args(**overrides):
    values = {
        'skip_docker': True,
        'skip_systemd': True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_install_load_dotenv_parses_simple_export_and_quotes(tmp_path):
    env_path = tmp_path / '.env'
    env_path.write_text(
        '\n'.join(
            [
                '# comment',
                'PLAIN=value',
                'export EXPORTED="quoted value"',
                "SINGLE='single quoted'",
                'IGNORED_WITHOUT_EQUALS',
            ]
        ),
        encoding='utf-8',
    )

    assert installer.load_dotenv(env_path) == {
        'PLAIN': 'value',
        'EXPORTED': 'quoted value',
        'SINGLE': 'single quoted',
    }


def test_install_update_dotenv_rewrites_existing_appends_missing_and_chmods(tmp_path):
    env_path = tmp_path / '.env'
    env_path.write_text(
        '# keep\nPWIKI_READ_ONLY=1\nexport GOOGLE_OAUTH_CLIENT_ID=old\n',
        encoding='utf-8',
    )

    installer.update_dotenv(
        env_path,
        {
            'PWIKI_READ_ONLY': '0',
            'GOOGLE_OAUTH_CLIENT_ID': 'client id',
            'PWIKI_URL_PREFIX': '/newwiki',
        },
    )

    assert env_path.read_text(encoding='utf-8') == (
        '# keep\n'
        'PWIKI_READ_ONLY=0\n'
        "GOOGLE_OAUTH_CLIENT_ID='client id'\n"
        '\n'
        'PWIKI_URL_PREFIX=/newwiki\n'
    )
    assert (env_path.stat().st_mode & 0o777) == 0o600


def test_install_normalize_url_helpers():
    assert installer.normalize_base_url(' https://example.com/ ') == 'https://example.com'
    assert installer.normalize_url_prefix('newwiki/') == '/newwiki'
    assert installer.normalize_url_prefix(' /nested/wiki/ ') == '/nested/wiki'
    assert installer.normalize_url_prefix(' / ') == ''


def test_install_validate_env_accepts_minimal_git_oauth_config(tmp_path):
    git_root = tmp_path / 'repo'
    markdown_root = git_root / 'wiki'
    (git_root / '.git').mkdir(parents=True)
    markdown_root.mkdir()
    (markdown_root / 'index.md').write_text('# Home\n', encoding='utf-8')
    env = {
        'PWIKI_SECRET_KEY': 'x' * 48,
        'PWIKI_USE_GIT': '1',
        'PWIKI_GIT_HOST_DIR': str(git_root),
        'PWIKI_MARKDOWN_SUBDIR': 'wiki',
        'PWIKI_READ_ONLY': '1',
        'GOOGLE_OAUTH_CLIENT_ID': 'client',
        'GOOGLE_OAUTH_CLIENT_SECRET': 'secret',
        'PWIKI_PUBLIC_BASE_URL': 'https://example.com',
        'PWIKI_URL_PREFIX': '/newwiki',
        # Pin the container UID to this process so the always-on UID check does
        # not emit a mismatch warning on hosts whose euid is not 1000.
        'PWIKI_UID': str(os.geteuid()) if hasattr(os, 'geteuid') else '1000',
    }

    problems, warnings = installer.validate_env(env, env, _args())

    assert problems == []
    assert warnings == []


def test_install_validate_env_rejects_public_base_url_path(tmp_path):
    git_root = tmp_path / 'repo'
    git_root.mkdir()
    (git_root / 'index.md').write_text('# Home\n', encoding='utf-8')
    env = {
        'PWIKI_SECRET_KEY': 'x' * 48,
        'PWIKI_USE_GIT': '0',
        'PWIKI_GIT_HOST_DIR': str(git_root),
        'PWIKI_READ_ONLY': '1',
        'PWIKI_ALLOW_ANONYMOUS': '1',
        'PWIKI_PUBLIC_BASE_URL': 'https://example.com/newwiki',
    }

    problems, _ = installer.validate_env(env, env, _args())

    assert 'PWIKI_PUBLIC_BASE_URL must be scheme+host only; put path in PWIKI_URL_PREFIX' in problems


def _bidirectional_env(git_root, markdown_root, **overrides):
    (git_root / '.git').mkdir(parents=True)
    markdown_root.mkdir()
    (markdown_root / 'index.md').write_text('# Home\n', encoding='utf-8')
    env = {
        'PWIKI_SECRET_KEY': 'x' * 48,
        'PWIKI_USE_GIT': '1',
        'PWIKI_GIT_HOST_DIR': str(git_root),
        'PWIKI_MARKDOWN_SUBDIR': markdown_root.name,
        'PWIKI_READ_ONLY': '0',
        'PWIKI_GIT_AUTO_COMMIT': '0',
        'PWIKI_GIT_HOST_PUSH': '1',
        'GOOGLE_OAUTH_CLIENT_ID': 'client',
        'GOOGLE_OAUTH_CLIENT_SECRET': 'secret',
    }
    env.update(overrides)
    return env


@pytest.mark.skipif(not hasattr(os, 'geteuid'), reason='POSIX UID check only')
def test_install_host_push_uid_match_has_no_uid_problem(tmp_path):
    env = _bidirectional_env(
        tmp_path / 'repo', tmp_path / 'repo' / 'wiki', PWIKI_UID=str(os.geteuid())
    )

    problems, _ = installer.validate_env(env, env, _args())

    assert problems == []


@pytest.mark.skipif(not hasattr(os, 'geteuid'), reason='POSIX UID check only')
def test_install_host_push_uid_mismatch_is_a_problem(tmp_path):
    env = _bidirectional_env(
        tmp_path / 'repo', tmp_path / 'repo' / 'wiki', PWIKI_UID=str(os.geteuid() + 1)
    )

    problems, _ = installer.validate_env(env, env, _args())

    assert any('needs the container UID to match the host' in p for p in problems)


@pytest.mark.skipif(not hasattr(os, 'geteuid'), reason='POSIX UID check only')
def test_install_read_only_uid_mismatch_is_a_warning_not_a_problem(tmp_path):
    # No host sync, but the non-root container still must write pwiki.db: a UID
    # mismatch is a warning here, not a hard problem.
    env = _bidirectional_env(
        tmp_path / 'repo',
        tmp_path / 'repo' / 'wiki',
        PWIKI_GIT_HOST_PUSH='0',
        PWIKI_READ_ONLY='1',
        PWIKI_UID=str(os.geteuid() + 1),
    )

    problems, warnings = installer.validate_env(env, env, _args())

    assert not any('container UID' in p for p in problems)
    assert any('volumes/pwiki-data' in w for w in warnings)


@pytest.mark.skipif(not hasattr(os, 'geteuid'), reason='POSIX UID check only')
def test_install_non_numeric_uid_is_a_problem(tmp_path):
    env = _bidirectional_env(
        tmp_path / 'repo', tmp_path / 'repo' / 'wiki', PWIKI_UID='bongdang'
    )

    problems, _ = installer.validate_env(env, env, _args())

    assert any('PWIKI_UID must be numeric' in p for p in problems)
