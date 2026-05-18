import os
import sys

import pytest
from typer.testing import CliRunner

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

import config
import db
from pwiki.cli import app


runner = CliRunner()


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "pwiki.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.reset_initialized_cache()
    yield db_path
    db.reset_initialized_cache()


def test_users_list_empty(fresh_db):
    result = runner.invoke(app, ["users", "list"])
    assert result.exit_code == 0
    assert "no users" in result.output


def test_users_grant_and_show(fresh_db):
    grant = runner.invoke(app, ["users", "grant", "alice@example.com", "--admin"])
    assert grant.exit_code == 0
    assert "granted" in grant.output

    show = runner.invoke(app, ["users", "show", "alice@example.com"])
    assert show.exit_code == 0
    assert "alice@example.com" in show.output


def test_users_grant_invalid_default_permission(fresh_db):
    result = runner.invoke(
        app, ["users", "grant", "x@example.com", "--default-permission", "garbage"]
    )
    assert result.exit_code != 0


def test_users_revoke(fresh_db):
    runner.invoke(app, ["users", "grant", "bob@example.com"])
    revoke = runner.invoke(app, ["users", "revoke", "bob@example.com"])
    assert revoke.exit_code == 0

    missing = runner.invoke(app, ["users", "revoke", "bob@example.com"])
    assert missing.exit_code != 0


def test_users_path_grant_and_revoke(fresh_db):
    runner.invoke(app, ["users", "grant", "carol@example.com"])
    grant = runner.invoke(
        app, ["users", "path-grant", "carol@example.com", "Private", "none"]
    )
    assert grant.exit_code == 0

    show = runner.invoke(app, ["users", "show", "carol@example.com"])
    assert "Private" in show.output
    assert "none" in show.output

    revoke = runner.invoke(
        app, ["users", "path-revoke", "carol@example.com", "Private"]
    )
    assert revoke.exit_code == 0
