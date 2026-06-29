from __future__ import annotations

from dataclasses import dataclass
import subprocess
from pathlib import Path


@dataclass(frozen=True)
class VaultEntry:
    path: Path
    rel_path: str
    title: str
    size: int


@dataclass(frozen=True)
class SearchHit:
    entry: VaultEntry
    line_number: int
    line: str


@dataclass(frozen=True)
class GitContext:
    content_root: Path
    git_root: Path
    scope: Path


@dataclass(frozen=True)
class GitStatus:
    context: GitContext
    branch: str
    changes: tuple[str, ...]

    @property
    def dirty(self) -> bool:
        return bool(self.changes)


class GitOperationError(RuntimeError):
    pass


def resolve_vault_root(root: str | Path) -> Path:
    vault_root = Path(root).expanduser().resolve()
    if not vault_root.exists():
        raise FileNotFoundError(f"Vault path does not exist: {vault_root}")
    if not vault_root.is_dir():
        raise NotADirectoryError(f"Vault path is not a directory: {vault_root}")
    return vault_root


def iter_markdown_files(root: str | Path) -> list[VaultEntry]:
    vault_root = resolve_vault_root(root)
    entries: list[VaultEntry] = []
    for path in sorted(vault_root.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(vault_root).parts):
            continue
        rel_path = path.relative_to(vault_root).as_posix()
        entries.append(
            VaultEntry(
                path=path,
                rel_path=rel_path,
                title=path.stem,
                size=path.stat().st_size,
            )
        )
    return entries


def scan_vault(root: str | Path) -> dict:
    vault_root = resolve_vault_root(root)
    markdown_files = iter_markdown_files(vault_root)
    attachment_count = 0
    directory_count = 0
    for path in vault_root.rglob("*"):
        rel_parts = path.relative_to(vault_root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if path.is_dir():
            directory_count += 1
        elif path.is_file() and path.suffix.lower() != ".md":
            attachment_count += 1
    return {
        "root": vault_root,
        "markdown_files": len(markdown_files),
        "directories": directory_count,
        "attachments": attachment_count,
        "bytes": sum(entry.size for entry in markdown_files),
    }


def build_tree(root: str | Path) -> dict:
    vault_root = resolve_vault_root(root)
    tree: dict = {}
    for entry in iter_markdown_files(vault_root):
        cursor = tree
        parts = entry.rel_path.split("/")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = None
    return tree


def search_vault(root: str | Path, query: str) -> list[SearchHit]:
    if not query:
        return []

    query_lower = query.lower()
    hits: list[SearchHit] = []
    for entry in iter_markdown_files(root):
        try:
            lines = entry.path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if query_lower in line.lower():
                hits.append(SearchHit(entry=entry, line_number=line_number, line=line.strip()))
    return hits


def resolve_git_context(root: str | Path) -> GitContext:
    content_root = resolve_vault_root(root)
    top_level = _run_git(
        content_root,
        ["rev-parse", "--show-toplevel"],
        check=True,
        error_message=f"{content_root} is not inside a Git working tree",
    )
    git_root = Path(top_level.stdout.strip()).resolve()
    try:
        scope = content_root.relative_to(git_root)
    except ValueError:
        raise GitOperationError(f"{content_root} is outside Git working tree {git_root}") from None
    return GitContext(content_root=content_root, git_root=git_root, scope=scope)


def git_status(root: str | Path, *, scoped: bool = True) -> GitStatus:
    context = resolve_git_context(root)
    command = ["status", "--porcelain=v1", "--branch", "--untracked-files=all"]
    if scoped and context.scope.parts:
        command.extend(["--", context.scope.as_posix()])

    result = _run_git(context.git_root, command, check=True)
    lines = [line for line in result.stdout.splitlines() if line]
    branch = lines[0].removeprefix("## ") if lines and lines[0].startswith("## ") else "(unknown)"
    return GitStatus(context=context, branch=branch, changes=tuple(lines[1:]))


def sync_git(root: str | Path) -> GitStatus:
    context = resolve_git_context(root)
    before = git_status(context.content_root, scoped=False)
    if before.dirty:
        raise GitOperationError("working tree is dirty; commit or discard changes before sync")

    _run_git(context.git_root, ["fetch"], check=True)
    _run_git(context.git_root, ["pull", "--ff-only"], check=True)
    return git_status(context.content_root)


def blocking_git_state(root: str | Path) -> str | None:
    context = resolve_git_context(root)
    git_dir_result = _run_git(context.git_root, ["rev-parse", "--git-dir"], check=True)
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = context.git_root / git_dir

    if (git_dir / "MERGE_HEAD").exists():
        return "Git merge is in progress"
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return "Git rebase is in progress"

    unmerged = _run_git(
        context.git_root,
        ["diff", "--name-only", "--diff-filter=U", "--", context.scope.as_posix() if context.scope.parts else "."],
        check=True,
    )
    if unmerged.stdout.strip():
        return "Git conflict is present"
    return None


def commit_git(
    root: str | Path,
    *,
    message: str,
    author_email: str | None = None,
    push: bool = False,
    rebase_before_push: bool = False,
) -> GitStatus:
    context = resolve_git_context(root)
    scope_arg = context.scope.as_posix() if context.scope.parts else "."

    staged_before = _staged_paths(context.git_root)
    outside_staged = [path for path in staged_before if not _path_is_in_scope(path, scope_arg)]
    if outside_staged:
        raise GitOperationError(
            "refusing to commit because staged changes exist outside the vault scope: "
            + ", ".join(outside_staged)
        )

    _run_git(context.git_root, ["add", "--", scope_arg], check=True)
    staged_after = _staged_paths(context.git_root)
    scoped_staged = [path for path in staged_after if _path_is_in_scope(path, scope_arg)]
    if not scoped_staged:
        raise GitOperationError("no vault changes to commit")

    if author_email:
        author_name = _author_name_from_email(author_email)
        commit_command = [
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-m",
            message,
            "--author",
            f"{author_name} <{author_email}>",
        ]
    else:
        commit_command = ["commit", "-m", message]
    _run_git(context.git_root, commit_command, check=True)

    if push:
        if rebase_before_push:
            _rebase_onto_upstream(context.git_root)
        _run_git(context.git_root, ["push"], check=True)

    return git_status(context.content_root)


def _rebase_onto_upstream(git_root: Path) -> None:
    """Replay local commits onto the tracking branch before a push.

    Heals the common divergence where a web commit lands on a base the remote
    has moved past (another editor or Obsidian pushed first): rebasing makes the
    later push a fast-forward. On a real content conflict the rebase is aborted
    so the working tree never lingers in a rebase/conflict state — the local
    commit is kept, the push is skipped, and the caller surfaces the error.

    No-ops when there is no upstream (a local-only repo, e.g. tests) or when the
    remote is unreachable, leaving the subsequent `push` to report the failure.
    """
    upstream = _run_git(
        git_root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        check=False,
    )
    if upstream.returncode != 0:
        return  # no tracking branch configured → nothing to rebase onto

    fetch = _run_git(git_root, ["fetch"], check=False)
    if fetch.returncode != 0:
        return  # offline / remote unreachable → let push surface the error

    rebase = _run_git(git_root, ["rebase", "@{upstream}"], check=False)
    if rebase.returncode != 0:
        _run_git(git_root, ["rebase", "--abort"], check=False)
        detail = (rebase.stderr or rebase.stdout).strip()
        raise GitOperationError(
            "auto-rebase onto upstream hit a conflict; the local commit was kept "
            "but not pushed. Resolve the divergence manually (e.g. in Obsidian) "
            "and sync again." + (f" [{detail}]" if detail else "")
        )


def default_commit_message(page_path: str | None = None) -> str:
    if page_path:
        return f"Update {page_path} via pwiki"
    return "Update vault via pwiki"


def _run_git(
    cwd: Path,
    args: list[str],
    *,
    check: bool,
    error_message: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise GitOperationError(str(exc)) from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitOperationError(error_message or detail or f"git {' '.join(args)} failed")
    return result


def _staged_paths(git_root: Path) -> list[str]:
    result = _run_git(git_root, ["diff", "--cached", "--name-only"], check=True)
    return [line for line in result.stdout.splitlines() if line]


def _path_is_in_scope(path: str, scope: str) -> bool:
    return scope == "." or path == scope or path.startswith(f"{scope}/")


def _author_name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0].strip()
    return local_part or "pwiki"
