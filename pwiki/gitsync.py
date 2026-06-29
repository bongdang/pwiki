"""Git status display + web-save auto-commit.

Reads scoped Git status for the sidebar/status bar (cached by HEAD/index/tree
mtime), reports merge/rebase/conflict states that must block a write, and
commits (optionally rebasing then pushing) after a successful web mutation when
`PWIKI_GIT_AUTO_COMMIT` is enabled. Delegates the actual Git work to `vault`.
"""

import os
from typing import Optional

from flask import session
from loguru import logger

import access
import config
from vault import (
    GitOperationError,
    blocking_git_state,
    commit_git,
    default_commit_message,
    git_status,
    resolve_git_context,
)

_git_status_cache = {'stamp': None, 'summary': None}


def _git_status_summary() -> Optional[dict]:
    if not config.GIT_ROOT:
        return None
    try:
        root = os.path.abspath(config.MARKDOWN_DIR)
        context = resolve_git_context(root)
        if os.path.realpath(os.path.abspath(config.GIT_ROOT)) != str(context.git_root):
            return None
        git_dir = os.path.join(context.git_root, '.git')
        # The index mtime does not change before `git add`, so it misses
        # unstaged edits. Including the vault tree mtime also invalidates this
        # cache after web saves or external editor changes.
        stamp = (
            root,
            str(context.git_root),
            os.path.getmtime(os.path.join(git_dir, 'HEAD')) if os.path.exists(git_dir) else 0,
            os.path.getmtime(os.path.join(git_dir, 'index')) if os.path.exists(os.path.join(git_dir, 'index')) else 0,
            _latest_tree_mtime(root),
        )
    except (OSError, FileNotFoundError, NotADirectoryError, GitOperationError):
        return None

    if _git_status_cache['stamp'] == stamp:
        return _git_status_cache['summary']

    try:
        status = git_status(root)
    except (FileNotFoundError, NotADirectoryError, GitOperationError):
        return None

    branch = status.branch or '(unknown)'
    summary = {
        'branch': branch,
        'dirty': status.dirty,
        'changes': len(status.changes),
        'label': f"{branch} · {'dirty' if status.dirty else 'clean'}",
    }
    # dict.update() changes both keys in one call, which is closer to atomic
    # than two separate assignments.
    _git_status_cache.update({'stamp': stamp, 'summary': summary})
    return summary


def _git_root_matches_config(root: str) -> bool:
    if not config.GIT_ROOT:
        return False
    try:
        context = resolve_git_context(root)
    except (FileNotFoundError, NotADirectoryError, GitOperationError):
        return False
    return os.path.realpath(os.path.abspath(config.GIT_ROOT)) == str(context.git_root)


def _blocking_git_write_state() -> Optional[str]:
    if not config.GIT_ROOT:
        return None
    root = os.path.abspath(config.MARKDOWN_DIR)
    if not _git_root_matches_config(root):
        return 'Configured Git root does not match the Markdown repository.'
    try:
        return blocking_git_state(root)
    except (FileNotFoundError, NotADirectoryError, GitOperationError) as exc:
        return str(exc)


def _auto_commit_change(commit_path: str) -> None:
    """Commit (and push) the vault scope after a successful web mutation.

    `commit_path` is the vault-relative path of the changed file, used only to
    build the commit message; `commit_git` stages the whole exposed vault scope,
    so a save, delete, or new attachment is all picked up the same way. A Git
    notice is stashed in the session for the next rendered page.
    """
    if not config.GIT_AUTO_COMMIT:
        return
    root = os.path.abspath(config.MARKDOWN_DIR)
    if not _git_root_matches_config(root):
        session['git_notice'] = {
            'level': 'error',
            'message': 'Git auto-commit skipped: PWIKI_GIT_ROOT does not match the Markdown repository.',
        }
        return

    author_email = access._current_email() or session.get('username') or None
    try:
        commit_git(
            root,
            message=default_commit_message(commit_path),
            author_email=author_email,
            push=True,
            rebase_before_push=config.GIT_AUTO_REBASE,
        )
    except GitOperationError as exc:
        logger.warning("git auto-commit/push failed for path={!r}: {}", commit_path, exc)
        session['git_notice'] = {
            'level': 'error',
            'message': f'Git auto-commit/push failed: {exc}',
        }
        return

    session['git_notice'] = {
        'level': 'ok',
        'message': 'Git auto-commit and push completed.',
    }


def _auto_commit_after_save(page_id: str) -> None:
    _auto_commit_change(f'{page_id}.md')


def _latest_tree_mtime(root: str) -> float:
    """Return the newest file/directory mtime inside the vault.

    Hidden entries starting with `.` are skipped, so `.git/` and `.obsidian/`
    changes do not affect this value. It should not be called more than once
    per request, but callers should remember each call is an O(N) tree walk.
    """
    try:
        latest = os.path.getmtime(root)
    except OSError:
        return 0.0
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not name.startswith('.')]
        for filename in filenames:
            if filename.startswith('.'):
                continue
            try:
                latest = max(latest, os.path.getmtime(os.path.join(current_root, filename)))
            except OSError:
                continue
    return latest
