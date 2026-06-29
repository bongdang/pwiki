#!/usr/bin/env python3
"""
pwiki - Flask Markdown wiki for Obsidian vaults

Thin entrypoint. The app is built by `factory.create_app()`; this module keeps
the `app:app` object that gunicorn and the test suite import, defines the
static-version helpers (which reference the module-global `app` because
`test_static_version_*` monkeypatch `app.app` and call them outside any request
context — the factory wires them into templates via a lazy import), and
re-exports the helpers that tests reach into by their historical `app.<name>`
paths. The real implementations live in the extracted modules (sections /
storage / rendering / access / gitsync / webutil / routes / factory).
"""

import os
from functools import lru_cache

from loguru import logger  # noqa: F401  (re-exported; tests use app.logger)

from dotenv_loader import load_cwd_dotenv

load_cwd_dotenv()

import config  # noqa: F401
from sections import (  # noqa: F401  (re-exported for tests/back-compat)
    _MD_FENCE_RE,
    _MD_HEADING_RE,
    _get_section,
    _heading_positions,
    _replace_section,
    _section_level,
    _section_range,
    _split_sections,
)
from storage import (  # noqa: F401  (re-exported for tests/back-compat)
    TreeNode,
    build_page_tree,
    create_dir,
    decorate_tree_for_render,
    default_new_page_id,
    get_all_pages,
    get_attachment_file,
    get_markdown_file,
    get_storage_page_file,
    get_upload_file,
    page_content_hash,
    quote_html,
    read_file,
    read_page,
    write_page,
    write_string_to_file,
    _apply_newline_style,
    _filter_tree_by_permission,
    _log_file_io,
    _safe_page_parts,
    _scan_all_pages,
)
from rendering import (  # noqa: F401  (re-exported for tests/back-compat)
    markdown_to_html,
    render_page,
    _is_safe_link_target,
    _is_tag_search_href,
    _normalize_obsidian_lookup_key,
    _obsidian_normalized_keys,
    _resolve_obsidian_page,
)
from access import (  # noqa: F401  (re-exported for tests/back-compat)
    generate_csrf_token,
    is_admin_allowed,
    is_edit_allowed,
    is_read_only,
    validate_csrf,
    _can_read,
    _can_write,
    _current_email,
)
from gitsync import (  # noqa: F401  (re-exported for tests/back-compat)
    _auto_commit_after_save,
    _auto_commit_change,
    _blocking_git_write_state,
    _git_root_matches_config,
    _git_status_summary,
    _latest_tree_mtime,
)
from webutil import (  # noqa: F401  (re-exported for tests/back-compat)
    csrf_invalid,
    ctx,
    git_blocked,
    invalid_page,
    json_error,
    redirect_to_page,
    write_failed,
    _enforce_read,
    _enforce_write,
    _login_redirect,
    _relative_time,
    _render_error,
    _render_forbidden,
    _render_save_conflict,
    _with_url_prefix,
)
from routes.oauth import _build_oauth_redirect_uri, _safe_oauth_next  # noqa: F401
from routes.pages import (  # noqa: F401  (re-exported for tests/back-compat)
    do_browse,
    do_delete,
    do_edit,
    do_save,
    do_upload,
    index,
    register_prefixed_routes,
    serve_attachment,
    serve_upload,
)
from factory import (  # noqa: F401  (re-exported for tests/back-compat)
    create_app,
    _configure_file_logging,
    _startup,
    _validate_markdown_scope,
)

app = create_app()


# ---------------------------------------------------------------------------
# Static-asset cache-busting version helper.
#
# Defined here (not in factory) and bound to the module-global `app` because
# `test_static_version_*` monkeypatch `app.app.static_folder` / `app.app.debug`
# and call these helpers outside any request context. The factory exposes them
# to templates via a lazy `from app import _static_version` in its
# `static_version` context processor, so a factory-built app renders correctly.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=32)
def _cached_static_version(filename: str) -> str:
    try:
        return str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
    except OSError:
        return '1'


def _static_version(filename: str) -> str:
    if app.debug or app.config.get('ENV') == 'development':
        try:
            return str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
        except OSError:
            return '1'
    return _cached_static_version(filename)


def _register_url_prefix_routes() -> None:
    """Back-compat shim: register the PWIKI_URL_PREFIX page/static mirrors on the
    live `app`. Tests reset `app._got_first_request` and call this directly after
    monkeypatching `config.URL_PREFIX`."""
    register_prefixed_routes(app)


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    port  = int(os.environ.get('FLASK_PORT', '5000'))
    app.run(host='0.0.0.0', debug=debug, port=port)
