from __future__ import annotations

from enum import Enum
import os
import sys
from pathlib import Path

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

# db.py / permissions.py / config.py use absolute imports (`import config`, etc.),
# so add the pwiki/ directory to sys.path for package-mode execution.
_PWIKI_DIR = Path(__file__).resolve().parent
if str(_PWIKI_DIR) not in sys.path:
    sys.path.insert(0, str(_PWIKI_DIR))


from dotenv_loader import load_cwd_dotenv

load_cwd_dotenv()

import config  # absolute import (PWIKI_DIR is on sys.path above)
import db
from vault import (
    GitOperationError,
    build_tree,
    commit_git,
    default_commit_message,
    git_status,
    scan_vault,
    search_vault,
    sync_git,
)


app = typer.Typer(help="pwiki operational CLI")
config_app = typer.Typer(help="Inspect pwiki configuration")
vault_app = typer.Typer(help="Inspect and search Markdown vaults")
users_app = typer.Typer(help="Manage OAuth users and path-based permissions")
app.add_typer(config_app, name="config")
app.add_typer(vault_app, name="vault")
app.add_typer(users_app, name="users")

console = Console()


class ThemeMode(str, Enum):
    light = "light"
    dark = "dark"
    system = "system"


@config_app.command("show")
def config_show() -> None:
    """Show the effective runtime configuration."""
    table = Table(title="pwiki configuration")
    table.add_column("Key", style="bold")
    table.add_column("Value")

    keys = [
        "SITE_NAME",
        "HOME_PAGE",
        "READ_ONLY",
        "FILE_IO_LOG",
        "GIT_AUTO_COMMIT",
        "DEFAULT_THEME",
        "URL_PREFIX",
        "PUBLIC_BASE_URL",
        "PWIKI_DIR",
        "DATA_DIR",
        "MARKDOWN_DIR",
        "GIT_ROOT",
        "UPLOAD_DIR",
        "DB_PATH",
    ]
    for key in keys:
        table.add_row(key, str(getattr(config, key, "")))
    console.print(table)


@config_app.command("theme")
def config_theme(
    mode: ThemeMode = typer.Argument(..., help="Default theme for first-time browsers"),
    shell: bool = typer.Option(False, "--shell", help="Print a POSIX shell export line"),
    compose: bool = typer.Option(False, "--compose", help="Print a docker-compose environment line"),
) -> None:
    """Choose the default web theme setting to use at process startup."""
    if shell and compose:
        raise typer.BadParameter("Use only one of --shell or --compose")

    if shell:
        console.print(f'export PWIKI_DEFAULT_THEME="{mode.value}"')
    elif compose:
        console.print(f'PWIKI_DEFAULT_THEME: "{mode.value}"')
    else:
        console.print(f"PWIKI_DEFAULT_THEME={mode.value}")


@vault_app.command("scan")
def vault_scan(root: Path = typer.Argument(..., help="Vault root directory")) -> None:
    """Summarize a Markdown vault."""
    try:
        result = scan_vault(root)
    except (FileNotFoundError, NotADirectoryError) as exc:
        logger.error(str(exc))
        raise typer.Exit(code=2) from exc

    table = Table(title="vault scan")
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("root", str(result["root"]))
    table.add_row("markdown_files", str(result["markdown_files"]))
    table.add_row("directories", str(result["directories"]))
    table.add_row("attachments", str(result["attachments"]))
    table.add_row("bytes", str(result["bytes"]))
    console.print(table)


@vault_app.command("tree")
def vault_tree(root: Path = typer.Argument(..., help="Vault root directory")) -> None:
    """Print the Markdown file tree."""
    try:
        data = build_tree(root)
    except (FileNotFoundError, NotADirectoryError) as exc:
        logger.error(str(exc))
        raise typer.Exit(code=2) from exc

    tree = Tree(str(root))
    _add_tree_nodes(tree, data)
    console.print(tree)


@vault_app.command("search")
def vault_search(
    root: Path = typer.Argument(..., help="Vault root directory"),
    query: str = typer.Argument(..., help="Case-insensitive text query"),
) -> None:
    """Search Markdown files in a vault."""
    try:
        hits = search_vault(root, query)
    except (FileNotFoundError, NotADirectoryError) as exc:
        logger.error(str(exc))
        raise typer.Exit(code=2) from exc

    table = Table(title=f"vault search: {query}")
    table.add_column("File", style="bold")
    table.add_column("Line", justify="right")
    table.add_column("Text")
    for hit in hits:
        table.add_row(hit.entry.rel_path, str(hit.line_number), hit.line)
    console.print(table)


@vault_app.command("git-status")
def vault_git_status(root: Path = typer.Argument(..., help="Vault root directory")) -> None:
    """Show Git status for a vault path inside a Git working tree."""
    try:
        status = git_status(root)
    except (FileNotFoundError, NotADirectoryError, GitOperationError) as exc:
        logger.error(str(exc))
        raise typer.Exit(code=2) from exc

    _print_git_status(status)


@vault_app.command("sync")
def vault_sync(root: Path = typer.Argument(..., help="Vault root directory")) -> None:
    """Fetch and fast-forward pull a clean Git working tree."""
    try:
        status = sync_git(root)
    except (FileNotFoundError, NotADirectoryError, GitOperationError) as exc:
        logger.error(str(exc))
        raise typer.Exit(code=2) from exc
    console.print("[green]sync complete[/green]")
    _print_git_status(status)


@vault_app.command("commit")
def vault_commit(
    root: Path = typer.Argument(..., help="Vault root directory"),
    message: str | None = typer.Option(None, "--message", "-m", help="Commit message"),
    page_path: str | None = typer.Option(
        None,
        "--page",
        help="Vault-relative page path used for the default commit message",
    ),
    author_email: str | None = typer.Option(
        None,
        "--author-email",
        help="Use this email as the Git commit author",
    ),
    push: bool = typer.Option(False, "--push", help="Push after a successful commit"),
) -> None:
    """Commit vault-scoped changes and optionally push."""
    try:
        status = commit_git(
            root,
            message=message or default_commit_message(page_path),
            author_email=author_email,
            push=push,
        )
    except (FileNotFoundError, NotADirectoryError, GitOperationError) as exc:
        logger.error(str(exc))
        raise typer.Exit(code=2) from exc
    console.print("[green]commit complete[/green]")
    _print_git_status(status)


def _print_git_status(status) -> None:
    table = Table(title="vault git status")
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("content_root", str(status.context.content_root))
    table.add_row("git_root", str(status.context.git_root))
    table.add_row("scope", status.context.scope.as_posix() if status.context.scope.parts else ".")
    table.add_row("branch", status.branch)
    table.add_row("dirty", "yes" if status.changes else "no")
    table.add_row("changes", str(len(status.changes)))
    console.print(table)

    if status.changes:
        changes_table = Table(title="working tree changes")
        changes_table.add_column("Status", style="bold")
        changes_table.add_column("Path")
        for line in status.changes:
            changes_table.add_row(line[:2], line[3:])
        console.print(changes_table)


_PERMISSION_VALUES = ("none", "read", "write")


class PermissionLevel(str, Enum):
    none = "none"
    read = "read"
    write = "write"


@users_app.command("list")
def users_list() -> None:
    """List all OAuth users."""
    rows = db.list_users()
    if not rows:
        console.print("(no users)")
        return
    table = Table(title="users")
    table.add_column("Email", style="bold")
    table.add_column("Admin", justify="center")
    table.add_column("Default")
    table.add_column("Sub")
    table.add_column("Last login")
    for r in rows:
        table.add_row(
            r["email"],
            "Y" if r["is_admin"] else "",
            r["default_permission"],
            r["sub"] or "(pending)",
            r["last_login_at"] or "",
        )
    console.print(table)


@users_app.command("grant")
def users_grant(
    email: str = typer.Argument(..., help="Google email to grant access to"),
    admin: bool = typer.Option(False, "--admin", help="Grant admin privileges"),
    default_permission: PermissionLevel = typer.Option(
        PermissionLevel.read,
        "--default-permission",
        help="Default permission outside any path override",
    ),
) -> None:
    """Add or update a user in the OAuth allow-list."""
    try:
        db.grant_user(
            email,
            is_admin=admin,
            default_permission=default_permission.value,
            granted_by="cli",
        )
    except ValueError as exc:
        logger.error(str(exc))
        raise typer.Exit(code=2) from exc
    console.print(
        f"granted [bold]{email}[/bold] (admin={admin}, default={default_permission.value})"
    )


@users_app.command("revoke")
def users_revoke(
    email: str = typer.Argument(..., help="Google email to remove"),
) -> None:
    """Remove a user (and all their path overrides) from the allow-list."""
    if not db.revoke_user(email):
        logger.error(f"user not found: {email}")
        raise typer.Exit(code=2)
    console.print(f"revoked [bold]{email}[/bold]")


@users_app.command("show")
def users_show(
    email: str = typer.Argument(..., help="Google email"),
) -> None:
    """Show a user's metadata and path overrides."""
    user = db.get_user_by_email(email)
    if not user:
        logger.error(f"user not found: {email}")
        raise typer.Exit(code=2)

    table = Table(title=f"user {email}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for k in (
        "email",
        "sub",
        "name",
        "is_admin",
        "default_permission",
        "created_at",
        "last_login_at",
        "granted_by",
    ):
        value = user.get(k)
        table.add_row(k, "" if value is None else str(value))
    console.print(table)

    paths = db.list_user_paths(email)
    if not paths:
        console.print("(no path overrides)")
        return
    ptable = Table(title="path overrides")
    ptable.add_column("Prefix", style="bold")
    ptable.add_column("Permission")
    ptable.add_column("Granted by")
    for p in paths:
        ptable.add_row(
            p["prefix"] or "(root)",
            p["permission"],
            p["granted_by"] or "",
        )
    console.print(ptable)


@users_app.command("path-grant")
def users_path_grant(
    email: str = typer.Argument(..., help="Google email"),
    prefix: str = typer.Argument(..., help="Vault-relative prefix (empty string = root)"),
    permission: PermissionLevel = typer.Argument(..., help="Permission to grant"),
) -> None:
    """Override a user's permission for a path prefix."""
    try:
        db.upsert_user_path(email, prefix, permission.value, granted_by="cli")
    except ValueError as exc:
        logger.error(str(exc))
        raise typer.Exit(code=2) from exc
    console.print(
        f"path-grant [bold]{email}[/bold] prefix={prefix!r} permission={permission.value}"
    )


@users_app.command("path-revoke")
def users_path_revoke(
    email: str = typer.Argument(..., help="Google email"),
    prefix: str = typer.Argument(..., help="Prefix override to remove"),
) -> None:
    """Remove a path override (default_permission applies again)."""
    if not db.delete_user_path(email, prefix):
        logger.error(f"no override for {email} prefix={prefix!r}")
        raise typer.Exit(code=2)
    console.print(f"path-revoke [bold]{email}[/bold] prefix={prefix!r}")


def _add_tree_nodes(parent: Tree, data: dict) -> None:
    for name, child in sorted(data.items()):
        if child is None:
            parent.add(name)
        else:
            branch = parent.add(name)
            _add_tree_nodes(branch, child)


if __name__ == "__main__":
    app()
