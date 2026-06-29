"""Page browsing/editing routes (the `pages` blueprint).

Holds the action dispatcher (`index`), the browse/edit/save/delete/upload/
add/search/preview handlers, attachment serving, `/healthz`, and the smart
landing fallback. `register_prefixed_routes(app)` mirrors these onto the
`PWIKI_URL_PREFIX` sub-path, matching the legacy behavior.
"""

import os
import re
import tempfile
import time
import unicodedata
from typing import Optional
from urllib.parse import quote

from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
)
from loguru import logger

import config
import oauth as oauth_module
import permissions
from access import (
    is_read_only,
    is_admin_allowed,
    validate_csrf,
    _can_read,
    _can_write,
    _current_email,
)
from gitsync import (
    _auto_commit_after_save,
    _auto_commit_change,
    _blocking_git_write_state,
)
from rendering import render_page
from sections import _get_section, _replace_section
from storage import (
    build_page_tree,
    default_new_page_id,
    get_all_pages,
    get_attachment_file,
    get_storage_page_file,
    get_upload_file,
    page_content_hash,
    quote_html,
    read_page,
    write_page,
    _filter_tree_by_permission,
)
from webutil import (
    csrf_invalid,
    ctx,
    git_blocked,
    invalid_page,
    json_error,
    redirect_to_page,
    write_failed,
    _enforce_read,
    _enforce_write,
    _relative_time,
    _render_error,
    _render_forbidden,
    _render_save_conflict,
    _with_url_prefix,
)

bp = Blueprint('pages', __name__)


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def do_browse(page_id: str):
    denied = _enforce_read(page_id)
    if denied is not None:
        return denied
    try:
        exists, page_ts, page_text = read_page(page_id)
    except ValueError:
        exists, page_ts, page_text = False, 0, ''

    if not exists:
        parts = [f'<p>Page <strong>{quote_html(page_id)}</strong> does not exist.</p>']
        if not is_read_only():
            # Use quote() for URL context and keep HTML attributes delimited
            # with double quotes. quote_html(html.escape) does not escape ',
            # so single-quoted attributes could allow HTML attribute injection.
            parts.append(
                f'<p><a href="{config.URL_PREFIX}/{quote(page_id, safe="/")}?action=edit">Create this page</a></p>'
            )
        html_content = ''.join(parts)
        return render_template('browse.html', html_content=html_content,
                               **ctx(page_id, f"Not found: {page_id}"))

    if page_ts == 0 and page_text == '':
        return _render_error(
            500,
            'Could not read this page',
            'A problem occurred while reading the Markdown file. Try again later or contact an administrator.',
            page_id,
        )

    last_edited        = time.ctime(page_ts) if page_ts else ''
    last_edited_rel    = _relative_time(page_ts)
    return render_template('browse.html',
                           html_content=render_page(page_text, page_id),
                           last_edited=last_edited,
                           last_edited_rel=last_edited_rel,
                           **ctx(page_id))


def do_edit(page_id: str):
    denied = _enforce_write(page_id, action='edit')
    if denied is not None:
        return denied

    old_text  = ''
    page_time = 0

    try:
        exists, page_time, old_text = read_page(page_id)
        if not exists:
            page_time = 0
            old_text = ''
    except ValueError:
        page_time = 0
        old_text = ''

    # Section editing: when ?section=N is present, show only that section text.
    section_str = request.args.get('section', '0')
    section_num = int(section_str) if section_str.isdigit() else 0
    edit_text   = _get_section(old_text, section_num) if section_num > 0 else old_text

    return render_template('edit.html',
                           old_text=edit_text,
                           page_time=page_time,
                           old_hash=page_content_hash(old_text) if exists else '',
                           section=section_num,
                           **ctx(page_id, f"Edit: {page_id}"))


def do_save(page_id: str, text: str, old_hash: str):
    denied = _enforce_write(page_id, action='edit')
    if denied is not None:
        return denied

    if not validate_csrf():
        return csrf_invalid(page_id)

    old_text    = ''
    page_time   = 0
    section_str = request.form.get('section', '0')
    section_num = int(section_str) if section_str.isdigit() else 0

    try:
        exists, page_time, old_text = read_page(page_id)
    except ValueError:
        return invalid_page(page_id)

    current_hash = page_content_hash(old_text) if exists else ''

    # For section edits, replace only that section and rebuild the full text.
    if section_num > 0:
        text = _replace_section(old_text, section_num, text)

    # Conflict check
    if exists and old_hash != current_hash and old_text != text:
        return _render_save_conflict(page_id, old_text, text, current_hash, page_time)

    if exists and old_text == text:
        return redirect_to_page(page_id)

    git_blocker = _blocking_git_write_state()
    if git_blocker:
        return git_blocked(page_id, 'save', git_blocker)

    try:
        latest_exists, latest_page_time, latest_text = read_page(page_id)
        latest_hash = page_content_hash(latest_text) if latest_exists else ''
        if latest_hash != current_hash:
            if latest_text == text:
                return redirect_to_page(page_id)
            return _render_save_conflict(page_id, latest_text, text, latest_hash, latest_page_time)
        write_page(page_id, text)
    except ValueError:
        return invalid_page(page_id)
    except OSError as exc:
        logger.exception("markdown write failed page_id={!r}: {}", page_id, exc)
        return write_failed(
            page_id,
            title_verb='save',
            fs_verb='write',
            consequence='Your changes were not saved.',
        )

    _auto_commit_after_save(page_id)
    return redirect_to_page(page_id)


def do_delete(page_id: str):
    denied = _enforce_write(page_id, action='delete')
    if denied is not None:
        return denied
    if not is_admin_allowed():
        return _render_forbidden(page_id, 'Only admins can delete pages.')

    if request.method == 'POST':
        if not validate_csrf():
            return csrf_invalid(page_id)

        git_blocker = _blocking_git_write_state()
        if git_blocker:
            return git_blocked(page_id, 'delete', git_blocker)

        try:
            page_file = get_storage_page_file(page_id)
            if os.path.exists(page_file):
                os.remove(page_file)
        except ValueError:
            return invalid_page(page_id)
        except OSError as exc:
            logger.exception("markdown delete failed page_id={!r}: {}", page_id, exc)
            return write_failed(
                page_id,
                title_verb='delete',
                fs_verb='delete',
                consequence='The file was left unchanged.',
            )

        # Mirror save behavior: commit+push the removal so the deletion
        # propagates and the server working tree is not left dirty (a dirty
        # tree blocks the host's `git pull --ff-only` sync).
        _auto_commit_after_save(page_id)
        return redirect_to_page()

    return render_template('delete.html', **ctx(page_id, f"Delete: {page_id}"))


def _safe_attachment_filename(raw_name: str) -> str:
    """Reduce an uploaded filename to a safe, vault-relative basename.

    Unicode is preserved (the vault uses Korean filenames, so
    werkzeug.secure_filename is too lossy), but directory components and
    filesystem-unsafe characters are stripped. Falls back to a timestamped name
    when nothing usable is left.
    """
    name = unicodedata.normalize('NFC', raw_name or '')
    # Drop any path components a browser might send (incl. Windows backslashes).
    name = name.replace('\\', '/').split('/')[-1]
    # Replace OS-forbidden / control characters AND the characters that are
    # special inside an Obsidian `![[...]]` embed (`#` anchor, `[`/`]` link
    # bounds, `^` block ref) so every uploaded file yields a working embed.
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f#\[\]^]', '_', name)
    name = name.strip().lstrip('.').strip()
    if not name:
        ext = os.path.splitext(raw_name or '')[1].lower()
        name = f"upload-{int(time.time())}{ext}"
    return name


def _unique_attachment_path(attach_dir: str, filename: str) -> str:
    """Absolute path inside attach_dir that does not collide, adding a
    `-1`/`-2`/... suffix to the stem when a file already exists.
    """
    stem, ext = os.path.splitext(filename)
    candidate = os.path.join(attach_dir, filename)
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(attach_dir, f"{stem}-{counter}{ext}")
        counter += 1
    return candidate


def _save_upload_atomic(file_storage, target: str) -> None:
    """Write an uploaded file to `target` via a same-directory temp file plus
    os.replace, so a failed write never leaves a partial attachment in place.
    """
    dir_path = os.path.dirname(target)
    fd, temp_path = tempfile.mkstemp(
        prefix=f'.{os.path.basename(target)}.',
        suffix='.tmp',
        dir=dir_path or None,
    )
    try:
        with os.fdopen(fd, 'wb') as f:
            file_storage.save(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, target)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def do_upload(page_id: str):
    """AJAX file/image upload from the editor. Saves into the vault attachment
    folder so the file is part of the Git-synced vault, then auto-commits like a
    page save. Every failure returns a JSON envelope with an accurate status.
    """
    if is_read_only():
        return json_error('Write mode is disabled (read-only).', 403)
    if not oauth_module.oauth_enabled() or not _current_email():
        return json_error('Sign in to upload files.', 403)
    if not _can_write(page_id):
        return json_error('You do not have permission to upload here.', 403)
    if not validate_csrf():
        return json_error('The security token is expired. Refresh the page and try again.', 403)

    git_blocker = _blocking_git_write_state()
    if git_blocker:
        return json_error(f'Git state blocks uploads: {git_blocker}', 409)

    file = request.files.get('file')
    if file is None or not file.filename:
        return json_error('No file was uploaded.', 400)

    filename = _safe_attachment_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in config.ATTACH_ALLOWED_EXTENSIONS:
        return json_error(f'File type is not allowed: {ext or "(none)"}', 400)

    vault_root = os.path.abspath(config.MARKDOWN_DIR)
    attach_dir = os.path.join(vault_root, *config.ATTACHMENT_SUBDIR.split('/'))
    try:
        os.makedirs(attach_dir, exist_ok=True)
        target = _unique_attachment_path(attach_dir, filename)
        # Final guard: the resolved target must stay inside the vault root.
        if os.path.commonpath([vault_root, os.path.abspath(target)]) != vault_root:
            return json_error('Invalid upload path.', 400)
        _save_upload_atomic(file, target)
    except OSError as exc:
        logger.exception("attachment write failed name={!r}: {}", filename, exc)
        return json_error('A filesystem write failed. The file was not saved.', 500)

    rel_path = f"{config.ATTACHMENT_SUBDIR}/{os.path.basename(target)}"
    _auto_commit_change(rel_path)
    # Pop the auto-commit notice so it travels with this JSON response instead of
    # leaking onto the next page render.
    notice = session.pop('git_notice', None)
    return jsonify({
        'ok': True,
        'path': rel_path,
        'embed': f'![[{rel_path}]]',
        'notice': notice,
    })


def do_add_page(current_page_id: str = ''):
    denied = _enforce_write(current_page_id, action='addpage')
    if denied is not None:
        return denied
    page_name = request.args.get('page', default_new_page_id(current_page_id))
    return render_template('addpage.html',
                           page_name=page_name,
                           **ctx(current_page_id, 'Add Page'))


def do_search():
    query   = request.args.get('q', '').strip()
    results = []

    if query:
        query_lower = query.lower()
        candidates = sorted(get_all_pages())
        if oauth_module.oauth_enabled():
            candidates = permissions.filter_visible_paths(_current_email(), candidates)
        for page_id in candidates:
            exists, _, text = read_page(page_id)
            if not exists:
                continue
            title_match = query_lower in page_id.lower()
            text_match  = query_lower in text.lower()
            if title_match or text_match:
                snippet = ''
                idx = text.lower().find(query_lower)
                if idx >= 0:
                    start   = max(0, idx - 60)
                    end     = min(len(text), idx + len(query) + 60)
                    snippet = (
                        ('…' if start > 0 else '')
                        + quote_html(text[start:end])
                        + ('…' if end < len(text) else '')
                    )
                results.append({'page': page_id, 'snippet': snippet,
                                'title_match': title_match})

    return render_template('search.html',
                           query=query, results=results,
                           **ctx('', f"Search: {query}"))


def do_preview(page_id: str):
    denied = _enforce_write(page_id, action='edit')
    if denied is not None:
        return denied
    if request.method == 'POST' and not validate_csrf():
        return ("The preview security token is expired. Refresh the page and try again.", 403)
    text = request.form.get('text', '')
    return render_page(text, page_id)


# ---------------------------------------------------------------------------
# Attachment serving + health
# ---------------------------------------------------------------------------

def _check_attachment_read(filename: str):
    if not oauth_module.oauth_enabled():
        return None
    email = _current_email()
    if not email:
        abort(403)
    raw_parent = os.path.dirname(filename or '').replace('\\', '/')
    # Collapse '..' segments before ACL evaluation. Reject anything escaping
    # the vault root.
    normalized = os.path.normpath('/' + raw_parent).lstrip('/')
    if normalized.startswith('..') or normalized == '..':
        abort(403)
    if not permissions.has_permission(email, normalized, 'read'):
        abort(403)
    return None


@bp.route('/upload_files/<path:filename>')
def serve_upload(filename: str):
    _check_attachment_read(filename)
    try:
        upload = get_upload_file(filename)
    except ValueError:
        abort(404)
    if not os.path.exists(upload) or not os.path.isfile(upload):
        abort(404)
    basename = os.path.basename(upload)
    return send_from_directory(os.path.dirname(upload), basename)


@bp.route('/attach/<path:filename>')
def serve_attachment(filename: str):
    _check_attachment_read(filename)
    try:
        attachment = get_attachment_file(filename)
    except ValueError:
        abort(404)
    if not os.path.exists(attachment) or not os.path.isfile(attachment):
        abort(404)
    basename = os.path.basename(attachment)
    return send_from_directory(os.path.dirname(attachment), basename)


@bp.route('/healthz')
def healthz():
    markdown_ok = os.path.isdir(config.MARKDOWN_DIR)
    return jsonify({
        'status': 'ok' if markdown_ok else 'degraded',
        'markdown_dir': markdown_ok,
        'read_only': config.READ_ONLY,
        'oauth_enabled': oauth_module.oauth_enabled(),
    }), 200 if markdown_ok else 503


def _resolve_landing_for(email: Optional[str]) -> Optional[str]:
    """Pick a sensible landing page for an authenticated user who has no read
    access to `config.HOME_PAGE`. Walks the permission-filtered page tree DFS,
    preferring an `index` file at the deepest folder where the user actually
    has access (matches the "index.md in my accessible folder" intent); falls back to the first
    readable file. Returns None when nothing is accessible.
    """
    if not email or not oauth_module.oauth_enabled():
        return None
    page_tree = build_page_tree()
    _filter_tree_by_permission(page_tree, email)
    if not page_tree:
        return None

    def find_in(nodes: list, parent_path: str) -> Optional[str]:
        expected_index = f"{parent_path}/index" if parent_path else 'index'
        for node in nodes:
            if node['type'] == 'file' and node['path'] == expected_index:
                return expected_index
        for node in nodes:
            if node['type'] == 'dir':
                picked = find_in(node['children'], node['path'])
                if picked:
                    return picked
        for node in nodes:
            if node['type'] == 'file':
                return node['path']
        return None

    return find_in(page_tree, '')


@bp.route('/', methods=['GET', 'POST'])
@bp.route('/<path:page_id>', methods=['GET', 'POST'])
def index(page_id: str = None):
    used_default_home = (page_id is None and 'id' not in request.args)
    if not page_id:
        page_id = request.args.get('id', config.HOME_PAGE)

    action = request.args.get('action', request.form.get('action', 'browse'))

    # Smart landing fallback: signed-in user with no read on HOME_PAGE gets
    # redirected to <first accessible folder>/index (if exists) or the first
    # readable page, instead of a 403 on every login.
    if (used_default_home and action == 'browse'
            and oauth_module.oauth_enabled()
            and _current_email()
            and not _can_read(page_id)):
        target = _resolve_landing_for(_current_email())
        if target:
            return redirect(f"{config.URL_PREFIX}/{quote(target, safe='/')}")
        return _render_forbidden(
            page_id,
            'You do not have access to any pages. Ask an administrator to grant access.',
        )

    handlers = {
        'browse':  lambda: do_browse(page_id),
        'edit':    lambda: do_edit(page_id),
        'save':    lambda: do_save(page_id,
                                   request.form.get('text', ''),
                                   request.form.get('oldhash', '')),
        'preview': lambda: do_preview(page_id),
        'delete':  lambda: do_delete(page_id),
        'addpage': lambda: do_add_page(page_id),
        'upload':  lambda: do_upload(page_id),
        'search':  do_search,
    }

    handler = handlers.get(action)
    if handler:
        return handler()
    return _render_error(400, 'Unknown Action', f"Unknown action: {action}", page_id)


def register_prefixed_routes(app) -> None:
    """Mirror the page/static/attachment routes onto PWIKI_URL_PREFIX so the app
    also serves under e.g. /newwiki when running behind a reverse-proxy sub-path.
    Idempotent: an already-registered prefixed endpoint is skipped.
    """
    if not config.URL_PREFIX:
        return

    def add_prefixed_rule(path: str, endpoint: str, view_func, **options) -> None:
        if endpoint in app.view_functions:
            return
        app.add_url_rule(path, endpoint=endpoint, view_func=view_func, **options)

    add_prefixed_rule(
        _with_url_prefix('/static/<path:filename>'),
        'prefixed_static',
        lambda filename: send_from_directory(app.static_folder, filename),
    )
    add_prefixed_rule(
        _with_url_prefix('/upload_files/<path:filename>'),
        'prefixed_upload_files',
        serve_upload,
    )
    add_prefixed_rule(
        _with_url_prefix('/attach/<path:filename>'),
        'prefixed_attach',
        serve_attachment,
    )
    add_prefixed_rule(
        _with_url_prefix('/healthz'),
        'prefixed_healthz',
        healthz,
    )
    add_prefixed_rule(
        config.URL_PREFIX,
        'prefixed_index_base',
        index,
        methods=['GET', 'POST'],
    )
    add_prefixed_rule(
        _with_url_prefix('/'),
        'prefixed_index_root',
        index,
        methods=['GET', 'POST'],
    )
    add_prefixed_rule(
        _with_url_prefix('/<path:page_id>'),
        'prefixed_index_page',
        index,
        methods=['GET', 'POST'],
    )
