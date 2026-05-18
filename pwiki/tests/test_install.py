import argparse
import os
import sys

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
