"""Test-wide setup.

Flask blocks add_url_rule after the first request, so OAuth routes are
registered once at import time. Set OAuth env before importing the app so it
starts with the routes registered. Tests replace oauth._oauth with fakes instead
of making external calls.
"""

import os
import sys

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

os.environ.setdefault('GOOGLE_OAUTH_CLIENT_ID', 'test-client-id')
os.environ.setdefault('GOOGLE_OAUTH_CLIENT_SECRET', 'test-client-secret')

import config  # noqa: E402

config.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
config.GOOGLE_OAUTH_CLIENT_SECRET = 'test-client-secret'

import app as _pwiki_app  # noqa: E402,F401  # registers OAuth routes
import db  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _default_admin(monkeypatch, tmp_path):
    """Most legacy tests assume an authenticated admin (session['username']='tester').
    Provision that user in an isolated DB so route-level ACL checks pass.
    Tests that need a different setup re-monkeypatch DB_PATH and grant manually.
    """
    db_path = str(tmp_path / 'pwiki_default.db')
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    db.reset_initialized_cache()
    db.grant_user('tester', is_admin=True, default_permission='write')
    yield
    db.reset_initialized_cache()
