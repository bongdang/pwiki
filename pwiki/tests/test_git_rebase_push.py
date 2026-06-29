"""commit_git(rebase_before_push=...) — heals divergence from concurrent edits.

These tests build a real bare remote with two clones (a "server" working tree
that pwiki commits into, and an "other" clone standing in for Obsidian / a second
editor) so the fetch/rebase/push path runs against actual Git.
"""

import os
import subprocess
import sys

import pytest

PWIKI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PWIKI_DIR not in sys.path:
    sys.path.insert(0, PWIKI_DIR)

import vault


def _git(cwd, *args, check=True):
    return subprocess.run(
        ['git', '-C', str(cwd), *args],
        check=check, capture_output=True, text=True,
    )


@pytest.fixture
def remote_and_clones(tmp_path):
    """A bare remote plus a 'server' clone and an 'other' clone sharing it."""
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)

    server = tmp_path / 'server'
    subprocess.run(['git', 'clone', str(remote), str(server)], check=True, capture_output=True)
    for key, val in (('user.name', 'server'), ('user.email', 'server@example.com')):
        _git(server, 'config', key, val)
    (server / 'note.md').write_text('line1\nline2\n', encoding='utf-8')
    _git(server, 'add', 'note.md')
    _git(server, 'commit', '-m', 'base')
    _git(server, 'push', 'origin', 'HEAD')

    other = tmp_path / 'other'
    subprocess.run(['git', 'clone', str(remote), str(other)], check=True, capture_output=True)
    for key, val in (('user.name', 'other'), ('user.email', 'other@example.com')):
        _git(other, 'config', key, val)

    return remote, server, other


def test_rebase_before_push_heals_nonconflicting_divergence(remote_and_clones):
    _remote, server, other = remote_and_clones

    # The "other" editor changes a different line and pushes first.
    (other / 'note.md').write_text('line1\nline2 edited by other\n', encoding='utf-8')
    _git(other, 'add', 'note.md')
    _git(other, 'commit', '-m', 'other edit')
    _git(other, 'push', 'origin', 'HEAD')

    # The server commits a non-conflicting change on the stale base, then pushes
    # with auto-rebase enabled.
    (server / 'extra.md').write_text('brand new\n', encoding='utf-8')
    status = vault.commit_git(
        str(server),
        message='server edit',
        author_email='server@example.com',
        push=True,
        rebase_before_push=True,
    )
    assert status is not None

    # Both edits are now on the remote and the server working tree is clean.
    assert _git(server, 'status', '--porcelain').stdout.strip() == ''
    log = _git(server, 'log', '--oneline').stdout
    assert 'server edit' in log
    assert 'other edit' in log


def test_rebase_before_push_aborts_on_conflict_and_keeps_local_commit(remote_and_clones):
    _remote, server, other = remote_and_clones

    # Both edit the SAME line → rebase will conflict.
    (other / 'note.md').write_text('line1\nLINE2-OTHER\n', encoding='utf-8')
    _git(other, 'add', 'note.md')
    _git(other, 'commit', '-m', 'other conflicting edit')
    _git(other, 'push', 'origin', 'HEAD')

    (server / 'note.md').write_text('line1\nLINE2-SERVER\n', encoding='utf-8')
    with pytest.raises(vault.GitOperationError) as exc:
        vault.commit_git(
            str(server),
            message='server conflicting edit',
            author_email='server@example.com',
            push=True,
            rebase_before_push=True,
        )
    assert 'conflict' in str(exc.value).lower()

    # Safe state: no lingering rebase, working tree clean, local commit kept and
    # NOT pushed.
    assert not (server / '.git' / 'rebase-merge').exists()
    assert not (server / '.git' / 'rebase-apply').exists()
    assert _git(server, 'status', '--porcelain').stdout.strip() == ''
    assert 'server conflicting edit' in _git(server, 'log', '--oneline').stdout
    # The remote still only has the other editor's commit.
    remote_log = _git(server, 'log', '--oneline', 'origin/main', check=False).stdout
    if not remote_log:  # default branch name fallback
        remote_log = _git(server, 'log', '--oneline', 'origin/master', check=False).stdout
    assert 'server conflicting edit' not in remote_log


def test_no_rebase_leaves_diverged_push_to_fail(remote_and_clones):
    _remote, server, other = remote_and_clones

    (other / 'note.md').write_text('line1\nline2 other\n', encoding='utf-8')
    _git(other, 'add', 'note.md')
    _git(other, 'commit', '-m', 'other edit')
    _git(other, 'push', 'origin', 'HEAD')

    # Without rebase_before_push, the diverged push is rejected (existing behavior).
    (server / 'extra.md').write_text('x\n', encoding='utf-8')
    with pytest.raises(vault.GitOperationError):
        vault.commit_git(
            str(server),
            message='server edit',
            author_email='server@example.com',
            push=True,
            rebase_before_push=False,
        )
    # Local commit is still kept.
    assert 'server edit' in _git(server, 'log', '--oneline').stdout
