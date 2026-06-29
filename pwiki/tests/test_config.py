import os
import subprocess
import sys

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)


def test_env_bool_truthy_and_falsy(monkeypatch):
    import config

    for truthy in ('1', 'true', 'TRUE', 'Yes', 'on', ' on '):
        monkeypatch.setenv('PWIKI_TEST_FLAG', truthy)
        assert config._env_bool('PWIKI_TEST_FLAG') is True

    for falsy in ('0', 'false', 'no', 'off', '', 'maybe'):
        monkeypatch.setenv('PWIKI_TEST_FLAG', falsy)
        assert config._env_bool('PWIKI_TEST_FLAG') is False

    monkeypatch.delenv('PWIKI_TEST_FLAG', raising=False)
    assert config._env_bool('PWIKI_TEST_FLAG', '1') is True
    assert config._env_bool('PWIKI_TEST_FLAG', '0') is False


def test_max_content_length_rejects_non_positive_values(monkeypatch):
    env = dict(os.environ)
    env['PYTHONPATH'] = PWIKI_DIR
    env['PWIKI_MAX_CONTENT_LENGTH'] = '0'
    result = subprocess.run(
        [sys.executable, '-c', 'import config; print(config.MAX_CONTENT_LENGTH)'],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.stdout.strip() == str(5 * 1024 * 1024)
