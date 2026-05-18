import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

from pwiki.cli import app


runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "pwiki operational CLI" in result.output


def test_config_show():
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "SITE_NAME" in result.output
    assert "READ_ONLY" in result.output
    assert "DEFAULT_THEME" in result.output


def test_config_theme_command():
    result = runner.invoke(app, ["config", "theme", "dark"])
    assert result.exit_code == 0
    assert "PWIKI_DEFAULT_THEME=dark" in result.output

    shell = runner.invoke(app, ["config", "theme", "light", "--shell"])
    assert shell.exit_code == 0
    assert 'export PWIKI_DEFAULT_THEME="light"' in shell.output


def test_config_show_loads_cwd_dotenv(tmp_path):
    (tmp_path / ".env").write_text("PWIKI_SITE_NAME=DotenvWiki\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO_DIR
    env.pop("PWIKI_SITE_NAME", None)

    result = subprocess.run(
        [sys.executable, "-m", "pwiki.cli", "config", "show"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "DotenvWiki" in result.stdout


def test_config_show_keeps_environment_over_dotenv(tmp_path):
    (tmp_path / ".env").write_text("PWIKI_SITE_NAME=DotenvWiki\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = REPO_DIR
    env["PWIKI_SITE_NAME"] = "EnvWiki"

    result = subprocess.run(
        [sys.executable, "-m", "pwiki.cli", "config", "show"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "EnvWiki" in result.stdout
    assert "DotenvWiki" not in result.stdout


def test_vault_scan_tree_search(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "Alpha.md").write_text("# Alpha\nhello vault\n", encoding="utf-8")
    (tmp_path / "Beta.md").write_text("# Beta\nother text\n", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"png")

    scan = runner.invoke(app, ["vault", "scan", str(tmp_path)])
    assert scan.exit_code == 0
    assert "markdown_files" in scan.output
    assert "2" in scan.output
    assert "attachments" in scan.output

    tree = runner.invoke(app, ["vault", "tree", str(tmp_path)])
    assert tree.exit_code == 0
    assert "Alpha.md" in tree.output
    assert "Beta.md" in tree.output

    search = runner.invoke(app, ["vault", "search", str(tmp_path), "vault"])
    assert search.exit_code == 0
    assert "notes/Alpha.md" in search.output
    assert "hello vault" in search.output


def test_vault_git_status_reports_dirty_tree(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "Note.md").write_text("# Note\n", encoding="utf-8")

    result = runner.invoke(app, ["vault", "git-status", str(tmp_path)])

    assert result.exit_code == 0
    assert "dirty" in result.output
    assert "yes" in result.output
    assert "Note.md" in result.output


def test_vault_git_status_accepts_parent_repo_subdirectory(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subdir = tmp_path / "vault"
    subdir.mkdir()
    (subdir / "Visible.md").write_text("# Visible\n", encoding="utf-8")
    (tmp_path / "Hidden.md").write_text("# Hidden\n", encoding="utf-8")

    result = runner.invoke(app, ["vault", "git-status", str(subdir)])

    assert result.exit_code == 0
    assert "content_root" in result.output
    assert "git_root" in result.output
    assert "vault" in result.output
    assert "Visible.md" in result.output
    assert "Hidden.md" not in result.output


def test_vault_commit_commits_only_vault_scope(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subdir = tmp_path / "vault"
    subdir.mkdir()
    (subdir / "Visible.md").write_text("# Visible\n", encoding="utf-8")
    (tmp_path / "Hidden.md").write_text("# Hidden\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "vault",
            "commit",
            str(subdir),
            "--author-email",
            "alice@example.com",
            "--page",
            "Visible.md",
        ],
    )

    assert result.exit_code == 0
    assert "commit complete" in result.output

    committed = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "--name-only", "--format=", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "vault/Visible.md" in committed.stdout
    assert "Hidden.md" not in committed.stdout


def test_vault_sync_refuses_dirty_tree(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "Note.md").write_text("# Note\n", encoding="utf-8")

    result = runner.invoke(app, ["vault", "sync", str(tmp_path)])

    assert result.exit_code == 2


def test_cli_can_scan_obsidata_when_present():
    repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    obsidata = os.path.join(repo_dir, "obsidata")
    if not os.path.isdir(obsidata):
        pytest.skip('obsidata directory not available')

    result = runner.invoke(app, ["vault", "scan", obsidata])

    assert result.exit_code == 0
    assert "markdown_files" in result.output
