import os
from pathlib import Path


REPO_ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


def test_dockerfile_installs_locked_requirements():
    dockerfile = (REPO_ROOT / 'pwiki' / 'Dockerfile').read_text(encoding='utf-8')

    assert 'COPY requirements.lock.txt .' in dockerfile
    assert 'pip install --no-cache-dir -r requirements.lock.txt' in dockerfile
    assert '-r requirements.txt gunicorn' not in dockerfile


def test_locked_requirements_include_gunicorn():
    lock = (REPO_ROOT / 'pwiki' / 'requirements.lock.txt').read_text(encoding='utf-8')

    assert 'gunicorn==' in lock
