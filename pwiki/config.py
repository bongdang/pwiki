# pwiki configuration file
import os

# Basic settings, overridable via environment variables.
SITE_NAME  = os.environ.get('PWIKI_SITE_NAME', 'MyWiki')
HOME_PAGE  = os.environ.get('PWIKI_HOME_PAGE', 'index')
# Flask session secret. Required in production. Falls back to PWIKI_HASH_KEY for
# one release window so existing deployments don't break before they update .env.
DEFAULT_DEV_SECRET = 'THISISTESTSECRET_FORTEST'
SECRET_KEY = (
    os.environ.get('PWIKI_SECRET_KEY')
    or os.environ.get('PWIKI_HASH_KEY')
    or DEFAULT_DEV_SECRET
)

# Directory paths
# If PWIKI_DATA_DIR is set, place all data directories underneath it.
# Otherwise keep the historical source-directory-relative behavior.
_src  = os.path.dirname(os.path.abspath(__file__))
_data = os.path.abspath(os.environ.get('PWIKI_DATA_DIR', _src))

PWIKI_DIR  = _src
DATA_DIR   = os.path.join(_data, 'data')
TEMP_DIR   = os.path.join(_data, 'temp')
HTML_DIR   = os.path.join(_data, 'html')
UPLOAD_DIR = os.path.join(_data, 'upload')
_repo_obsidata = os.path.normpath(os.path.join(_src, '..', 'obsidata'))
_default_markdown_dir = _repo_obsidata if os.path.isdir(_repo_obsidata) else os.path.join(_data, 'markdown')
MARKDOWN_DIR = os.environ.get('PWIKI_MARKDOWN_DIR', _default_markdown_dir)

UPLOAD_URL = '/upload_files'
ATTACH_URL = '/attach'
ATTACH_ALLOWED_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.webp',
    '.pdf', '.txt', '.md', '.zip',
}

# SQLite database for users/permissions (replaces users/<u>.json over C8).
DB_PATH = os.environ.get('PWIKI_DB_PATH', os.path.join(_data, 'pwiki.db'))

# Optional Git working tree root. PWIKI_MARKDOWN_DIR may point at a subdirectory
# inside this tree so pwiki only exposes that subtree while Git operations can
# still see the repository metadata at the root.
GIT_ROOT = os.environ.get('PWIKI_GIT_ROOT', '')

# Google OAuth (active only when both client id and secret are set).
GOOGLE_OAUTH_CLIENT_ID     = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', '')
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', '')
ADMIN_GOOGLE_EMAIL         = os.environ.get('PWIKI_ADMIN_GOOGLE_EMAIL', '').strip().lower()

# Public base URL (scheme + host, no trailing slash) used to build OAuth redirect URIs
# when running behind a reverse proxy. Falls back to request.host_url if empty.
PUBLIC_BASE_URL = os.environ.get('PWIKI_PUBLIC_BASE_URL', '').rstrip('/')

# Sub-path prefix when deployed behind a reverse proxy (e.g. nginx location /newwiki)
# Set PWIKI_URL_PREFIX=/newwiki in environment. Must NOT end with a slash.
URL_PREFIX = os.environ.get('PWIKI_URL_PREFIX', '').rstrip('/')
_default_theme = os.environ.get('PWIKI_DEFAULT_THEME', 'system').lower()
DEFAULT_THEME = _default_theme if _default_theme in {'light', 'dark', 'system'} else 'system'

# Feature flags
USE_INDEX     = 1

# Runtime is markdown-only.
STORAGE_BACKEND = 'markdown'
MARKUP_MODE = 'markdown'

# Markdown defaults to read-only until write-mode policy is finalized.
READ_ONLY = os.environ.get('PWIKI_READ_ONLY', '1').lower() in {'1', 'true', 'yes', 'on'}
FILE_IO_LOG = os.environ.get('PWIKI_FILE_IO_LOG', '0').lower() in {'1', 'true', 'yes', 'on'}
LOG_FILE = os.environ.get('PWIKI_LOG_FILE', '').strip()
LOG_ROTATION = os.environ.get('PWIKI_LOG_ROTATION', '10 MB').strip() or '10 MB'
LOG_RETENTION = os.environ.get('PWIKI_LOG_RETENTION', '14 days').strip() or '14 days'
GIT_AUTO_COMMIT = os.environ.get('PWIKI_GIT_AUTO_COMMIT', '0').lower() in {'1', 'true', 'yes', 'on'}
# Anonymous read-only fallback is opt-in. Without this flag, startup refuses when
# OAuth is not configured so that an empty .env never silently exposes the vault.
ALLOW_ANONYMOUS = os.environ.get('PWIKI_ALLOW_ANONYMOUS', '0').lower() in {'1', 'true', 'yes', 'on'}

# Max request body size in bytes. Default 5 MB — large enough for a single
# Markdown page plus a few embedded images, small enough to fail loud on a
# malicious oversize POST. Override with PWIKI_MAX_CONTENT_LENGTH in bytes.
try:
    MAX_CONTENT_LENGTH = int(os.environ.get('PWIKI_MAX_CONTENT_LENGTH', str(5 * 1024 * 1024)))
except ValueError:
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
if MAX_CONTENT_LENGTH <= 0:
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024

# Session cookie security. Auto-derive Secure=True when PWIKI_PUBLIC_BASE_URL is
# https://, so HTTPS deployments don't have to remember to flip an extra flag.
# PWIKI_SESSION_COOKIE_SECURE=1|0 can override the auto-detection.
_session_secure_env = os.environ.get('PWIKI_SESSION_COOKIE_SECURE', '').strip().lower()
if _session_secure_env in {'1', 'true', 'yes', 'on'}:
    SESSION_COOKIE_SECURE = True
elif _session_secure_env in {'0', 'false', 'no', 'off'}:
    SESSION_COOKIE_SECURE = False
else:
    SESSION_COOKIE_SECURE = PUBLIC_BASE_URL.lower().startswith('https://')
SESSION_COOKIE_SAMESITE = os.environ.get('PWIKI_SESSION_COOKIE_SAMESITE', 'Lax')

# HTTP settings
HTTP_CHARSET  = 'utf-8'
URL_PROTOCOLS = 'http|https|ftp|mailto'

# Internal read-only API (separate from the OAuth web UI). When the token is
# empty the entire /api/internal/* surface is disabled (returns 404). The CIDR
# allowlist defaults to loopback + RFC1918 ranges so accidental public exposure
# still fails closed. Trusted-proxy CIDRs is an explicit opt-in for honoring
# X-Forwarded-For from a reverse proxy in front of the API.
INTERNAL_API_TOKEN = os.environ.get('PWIKI_INTERNAL_API_TOKEN', '').strip()
INTERNAL_API_ALLOWED_CIDRS = os.environ.get('PWIKI_INTERNAL_API_ALLOWED_CIDRS', '').strip()
INTERNAL_API_TRUSTED_PROXY_CIDRS = os.environ.get('PWIKI_INTERNAL_API_TRUSTED_PROXY_CIDRS', '').strip()
