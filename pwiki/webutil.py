"""Response-building helpers shared by the route layer.

Builds the Jinja template context (`ctx`), renders the shared error/forbidden/
conflict pages, performs active read/write enforcement (returning a response or
`None`), and provides the small DRY helpers that collapse the error responses
re-typed across the save/delete/upload handlers. Imports `access`, `gitsync`,
and `storage` one-way; the route modules import this.
"""

import time
from typing import Optional
from urllib.parse import quote

from flask import jsonify, redirect, render_template, request, session

import access
import config
import gitsync
import oauth as oauth_module
import storage


def _with_url_prefix(path: str) -> str:
    return f"{config.URL_PREFIX}{path}"


def _relative_time(ts: float) -> str:
    if not ts:
        return ''
    diff = max(0, time.time() - ts)
    if diff < 60:
        return 'just now'
    if diff < 3600:
        minutes = int(diff // 60)
        return f'{minutes} minute{"s" if minutes != 1 else ""} ago'
    if diff < 86400:
        hours = int(diff // 3600)
        return f'{hours} hour{"s" if hours != 1 else ""} ago'
    if diff < 86400 * 7:
        days = int(diff // 86400)
        return f'{days} day{"s" if days != 1 else ""} ago'
    if diff < 86400 * 30:
        weeks = int(diff // 86400 // 7)
        return f'{weeks} week{"s" if weeks != 1 else ""} ago'
    if diff < 86400 * 365:
        months = int(diff // 86400 // 30)
        return f'{months} month{"s" if months != 1 else ""} ago'
    years = int(diff // 86400 // 365)
    return f'{years} year{"s" if years != 1 else ""} ago'


def ctx(page_id: str = '', title: str = '') -> dict:
    page_tree = storage.build_page_tree()
    storage.decorate_tree_for_render(page_tree, page_id)
    oauth_active = oauth_module.oauth_enabled()
    if oauth_active:
        storage._filter_tree_by_permission(page_tree, access._current_email())
    git_summary = gitsync._git_status_summary()
    git_notice = session.pop('git_notice', None)
    return {
        'site_name':         config.SITE_NAME,
        'charset':           config.HTTP_CHARSET,
        'page_id':           page_id,
        'title':             title or page_id.replace('_', ' ') or config.SITE_NAME,
        'use_index':         config.USE_INDEX,
        'edit_allowed':      access.is_edit_allowed(),
        'admin_allowed':     access.is_admin_allowed(),
        'username':          session.get('username', ''),
        'is_admin':          bool(session.get('is_admin')),
        'is_logged_in':      bool(session.get('username')),
        'any_auth_required': True,
        'url_prefix':        config.URL_PREFIX,
        'storage_backend':   config.STORAGE_BACKEND,
        'markup_mode':       config.MARKUP_MODE,
        'read_only':         access.is_read_only(),
        'default_theme':     config.DEFAULT_THEME,
        'page_tree':         page_tree,
        'total_pages':       len(storage.get_all_pages()),
        'oauth_enabled':     oauth_active,
        'git_status':        git_summary,
        'git_notice':        git_notice,
        'can_read':          access._can_read(page_id) if page_id else True,
        'can_write':         access._can_write(page_id) if page_id else False,
    }


def _login_redirect(page_id: str, action: str):
    if not oauth_module.oauth_enabled():
        return _render_forbidden(
            page_id,
            'Authentication is not configured, so write operations are unavailable. Contact an administrator.',
        )
    next_path = (
        f"{config.URL_PREFIX}/{quote(page_id, safe='/')}?action={action}" if page_id
        else f"{config.URL_PREFIX}/"
    )
    # Encode the whole next_path so the ?action= part is not parsed as a
    # parameter of the login URL.
    return redirect(f"{_with_url_prefix('/auth/google/login')}?next={quote(next_path, safe='/?=&')}")


def _render_forbidden(page_id: str, message: str):
    return render_template(
        'error_403.html',
        message=message,
        **ctx(page_id, 'Forbidden'),
    ), 403


def _render_error(status_code: int, title: str, message: str, page_id: str = '', detail: str = ''):
    return render_template(
        'error.html',
        error_title=title,
        error_message=message,
        error_detail=detail,
        **ctx(page_id, title),
    ), status_code


def _render_save_conflict(page_id: str, old_text: str, new_text: str, current_hash: str, page_time: int):
    now = int(time.time())
    return render_template('conflict.html',
                           old_text=old_text,
                           new_text=new_text,
                           current_hash=current_hash,
                           saved_time=time.ctime(page_time),
                           current_time=time.ctime(now),
                           new_time=now,
                           **ctx(page_id, f"Edit Conflict: {page_id}"))


def _enforce_read(page_id: str):
    if not oauth_module.oauth_enabled():
        return None  # anonymous read-only mode allows browsing
    if not access._current_email():
        return _login_redirect(page_id, 'browse')
    if not access._can_read(page_id):
        return _render_forbidden(page_id, 'You do not have permission to read this page.')
    return None


def _enforce_write(page_id: str, action: str = 'edit'):
    if access.is_read_only():
        return _render_forbidden(page_id, 'Write mode is disabled (read-only).')
    if not oauth_module.oauth_enabled():
        return _login_redirect(page_id, action)  # no auth backend → block
    if not access._current_email():
        return _login_redirect(page_id, action)
    if not access._can_write(page_id):
        return _render_forbidden(page_id, 'You do not have permission to write this page.')
    return None


# ---------------------------------------------------------------------------
# Shared error responses (collapse the copy-paste across save/delete/upload)
# ---------------------------------------------------------------------------

def redirect_to_page(page_id: str = ''):
    """Redirect to a vault page (or the root when page_id is empty), honoring
    PWIKI_URL_PREFIX."""
    return redirect(f"{config.URL_PREFIX}/{page_id}")


def csrf_invalid(page_id: str = ''):
    return _render_error(403, config.MSG_CSRF_INVALID_TITLE, config.MSG_CSRF_INVALID, page_id)


def invalid_page(page_id: str = ''):
    return _render_error(400, config.MSG_INVALID_PAGE_TITLE, config.MSG_INVALID_PAGE, page_id)


def git_blocked(page_id: str, verb: str, blocker: str):
    """409 page for a save/delete refused because of a merge/rebase/conflict
    Git state. `verb` is the imperative ('save'/'delete')."""
    present = {'save': 'saving', 'delete': 'deleting'}.get(verb, f'{verb}ing')
    return _render_error(
        409,
        f'Cannot {verb} because of Git state',
        f'Resolve the merge, rebase, or conflict state before {present} again.',
        page_id,
        f'Git state blocks this {verb}: {blocker}',
    )


def write_failed(page_id: str, *, title_verb: str, fs_verb: str, consequence: str):
    """500 page for a filesystem write/delete failure. `title_verb` names the
    user action ('save'/'delete'); `fs_verb` names the filesystem op
    ('write'/'delete')."""
    return _render_error(
        500,
        f'Could not {title_verb} this page',
        f'A filesystem {fs_verb} failed. {consequence}',
        page_id,
    )


def json_error(message: str, status: int):
    return jsonify({'ok': False, 'error': message}), status
