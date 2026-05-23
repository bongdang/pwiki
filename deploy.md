# pwiki Deployment Guide

This guide matches the public README: pwiki is a small Flask Markdown wiki that
reads an Obsidian vault directly from disk. Obsidian remains the source of truth;
pwiki adds browser reading, selective sharing, optional restricted web edits, and
Git-backed sync helpers.

pwiki does not import notes into a database. Your Markdown files stay where they
are, and the app reads only the vault folder or subfolder you choose to expose.

## Deployment Model

The standard deployment path is Docker Compose.

- `PWIKI_GIT_HOST_DIR` is the host path to your Obsidian vault Git working tree.
- `PWIKI_MARKDOWN_SUBDIR` is the optional subfolder inside that vault to expose.
- Compose mounts the host vault at `/data/git` inside the container.
- Compose sets `PWIKI_MARKDOWN_DIR=/data/git/${PWIKI_MARKDOWN_SUBDIR:-}`.
- SQLite app state, such as OAuth users and path permissions, lives under `/data/pwiki`.

`./obsidata` is only the local default fallback used by `docker-compose.yml` when
`PWIKI_GIT_HOST_DIR` is not set. For a real deployment, point
`PWIKI_GIT_HOST_DIR` at your actual Obsidian vault or at a separate Git checkout
of that vault.

Safe patterns:

- Read-only pwiki next to a live Obsidian vault.
- Write-enabled pwiki on a separate Git checkout, then sync back into Obsidian.

Avoid letting Obsidian and pwiki write the same file at the same time.

## Requirements

- Python 3.11 or newer for local runs. Python 3.13 is the most-tested target.
- Docker and Docker Compose for the standard deployment path.
- An Obsidian vault on disk. Git is recommended if you want sync/commit/push.
- Linux or macOS host. Windows is untested.

## 1. Clone

```bash
git clone https://github.com/bongdang/pwiki.git
cd pwiki
```

If you are deploying from a private/internal mirror, use that repository URL
instead. The directory name should be `pwiki` for the examples below.

## 2. Prepare the Vault

You can point pwiki at your real Obsidian vault:

```env
PWIKI_GIT_HOST_DIR=/srv/obsidian/my-vault
PWIKI_MARKDOWN_SUBDIR=
```

Or expose only one folder inside the vault:

```env
PWIKI_GIT_HOST_DIR=/srv/obsidian/my-vault
PWIKI_MARKDOWN_SUBDIR=Shared/Family
```

In the second example, pwiki reads only:

```text
/srv/obsidian/my-vault/Shared/Family
```

inside the container this becomes:

```text
/data/git/Shared/Family
```

Important: `PWIKI_MARKDOWN_SUBDIR` is always relative to
`PWIKI_GIT_HOST_DIR`. Do not include unrelated parent directories in the
subdir value.

If you prefer a separate checkout for server use:

```bash
git clone <your-private-vault-repo> /srv/pwiki-vault
```

then use:

```env
PWIKI_GIT_HOST_DIR=/srv/pwiki-vault
PWIKI_MARKDOWN_SUBDIR=
```

Before starting the app, verify that Markdown files are visible on the host:

```bash
find "${PWIKI_GIT_HOST_DIR}/${PWIKI_MARKDOWN_SUBDIR}" -name '*.md' | head
```

## 3. Configure `.env`

Start from the template:

```bash
cp .env.example .env
$EDITOR .env
chmod 600 .env
```

At minimum, set:

```env
PWIKI_SECRET_KEY=CHANGE_THIS_TO_A_LONG_RANDOM_SECRET
PWIKI_GIT_HOST_DIR=/srv/obsidian/my-vault
PWIKI_MARKDOWN_SUBDIR=
PWIKI_READ_ONLY=1
```

Generate a strong secret:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

For Google OAuth:

```env
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
PWIKI_ADMIN_GOOGLE_EMAIL=you@example.com
PWIKI_PUBLIC_BASE_URL=https://wiki.example.com
```

For intentional anonymous read-only mode without OAuth:

```env
PWIKI_ALLOW_ANONYMOUS=1
```

Without OAuth and without `PWIKI_ALLOW_ANONYMOUS=1`, startup refuses to boot so
an unconfigured `.env` cannot silently expose your vault.

For sub-path reverse proxy deployment:

```env
PWIKI_PUBLIC_BASE_URL=https://wiki.example.com
PWIKI_URL_PREFIX=/newwiki
```

`PWIKI_PUBLIC_BASE_URL` must be only scheme plus host. Put the path prefix in
`PWIKI_URL_PREFIX`.

## 4. Run the Installer

The installer validates `.env`, asks onboarding questions when needed, can
install a user systemd sync timer, and can run Docker Compose:

```bash
./install.sh
```

Non-interactive examples:

```bash
./install.sh --yes
./install.sh --anonymous --read-only --no-git --yes
./install.sh --oauth --read-only --git --no-auto-commit --yes
./install.sh --oauth --write --git --auto-commit --yes
./install.sh --oauth --nginx --public-base-url https://wiki.example.com --url-prefix /newwiki
```

Skip selected steps:

```bash
python3 install.py --skip-systemd
python3 install.py --skip-docker
```

The installer checks:

- `PWIKI_SECRET_KEY` is set and not the development default.
- `PWIKI_GIT_HOST_DIR` exists and is a Git working tree when Git mode is enabled.
- `PWIKI_MARKDOWN_SUBDIR` resolves inside `PWIKI_GIT_HOST_DIR`.
- OAuth is fully configured, or anonymous read-only mode is explicitly enabled.
- `PWIKI_GIT_AUTO_COMMIT=1` is used only with write mode and a writable vault mount.
- reverse-proxy URL settings are split correctly between `PWIKI_PUBLIC_BASE_URL`
  and `PWIKI_URL_PREFIX`.

## 5. Run Manually with Docker Compose

If you do not use the installer:

```bash
docker compose up -d --build
docker compose logs -f pwiki
```

Check the container configuration:

```bash
docker compose run --rm pwiki python -m pwiki.cli config show
docker compose run --rm pwiki python -m pwiki.cli vault git-status "$PWIKI_MARKDOWN_DIR"
```

Initial smoke checks:

1. Open `http://<server>:5000/`.
2. Confirm the sidebar shows the expected Markdown files.
3. If OAuth is enabled, confirm login redirects to Google.
4. If `PWIKI_READ_ONLY=1`, confirm write actions are blocked.

When `PWIKI_URL_PREFIX=/newwiki`, test through the reverse proxy path
`/newwiki/` instead of direct `:5000/` access.

## 6. Important Environment Variables

| Name | Purpose |
|---|---|
| `PWIKI_SECRET_KEY` | Flask session secret. Required in production. |
| `PWIKI_GIT_HOST_DIR` | Host path to the Obsidian vault Git working tree. |
| `PWIKI_MARKDOWN_SUBDIR` | Optional vault subfolder to expose. Empty exposes the whole mounted vault. |
| `PWIKI_READ_ONLY` | `1` blocks all web writes. This is the default. |
| `PWIKI_ALLOW_ANONYMOUS` | `1` allows OAuth-less anonymous read-only mode. |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client id. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret. |
| `PWIKI_ADMIN_GOOGLE_EMAIL` | First admin email seeded at startup. |
| `PWIKI_PUBLIC_BASE_URL` | External scheme and host for OAuth redirect URI generation. |
| `PWIKI_URL_PREFIX` | Sub-path prefix such as `/newwiki`. |
| `PWIKI_GIT_AUTO_COMMIT` | `1` commits and pushes after successful web saves. |
| `PWIKI_VAULT_MOUNT_MODE` | Compose volume mode for the vault, usually `rw` or `ro`. |
| `PWIKI_LOG_FILE` | Optional rotating log file path, for example `/data/pwiki/pwiki.log`. |
| `PWIKI_MAX_CONTENT_LENGTH` | Flask request body limit in bytes. Default is 5 MiB. |
| `PWIKI_INTERNAL_API_TOKEN` | Bearer token for `/api/internal/*`. Empty disables the internal API entirely. |
| `PWIKI_INTERNAL_API_ALLOWED_CIDRS` | CIDR allowlist for the internal API. Defaults to loopback + RFC1918. |
| `PWIKI_INTERNAL_API_TRUSTED_PROXY_CIDRS` | CIDRs whose `X-Forwarded-For` may be honored when computing the client IP. Empty = always use `remote_addr`. |

## 7. Read-Only, Writes, and Mount Mode

Read-only operation is recommended by default:

```env
PWIKI_READ_ONLY=1
PWIKI_VAULT_MOUNT_MODE=ro
```

For web edits:

```env
PWIKI_READ_ONLY=0
PWIKI_VAULT_MOUNT_MODE=rw
```

Write access still requires OAuth plus per-user `write` permission. Anonymous
mode is always read-only.

## 8. Google OAuth

Create a Google OAuth Web application in:

```text
https://console.cloud.google.com/apis/credentials
```

Authorized redirect URI examples:

```text
https://wiki.example.com/auth/google/callback
https://wiki.example.com/newwiki/auth/google/callback
http://localhost:5000/auth/google/callback
```

Use HTTPS for production. Localhost is the only normal HTTP exception.

Admin/user management:

```bash
docker compose exec pwiki sh -lc 'python -m pwiki.cli users list'
docker compose exec pwiki sh -lc 'python -m pwiki.cli users grant alice@example.com --default-permission read'
docker compose exec pwiki sh -lc 'python -m pwiki.cli users grant admin2@example.com --admin --default-permission write'
docker compose exec pwiki sh -lc 'python -m pwiki.cli users path-grant alice@example.com Shared/Family read'
docker compose exec pwiki sh -lc 'python -m pwiki.cli users path-grant alice@example.com Private none'
docker compose exec pwiki sh -lc 'python -m pwiki.cli users show alice@example.com'
```

Admins can also manage users from `/_admin/users`, or
`/<PWIKI_URL_PREFIX>/_admin/users` when deployed under a sub-path.

## 8b. Internal Read-Only API

A separate read-only JSON API is available at `/api/internal/*` for same-host
or private-network consumers (typical use: an internal AI assistant that needs
to search and read documents without going through Google OAuth). The surface
is disabled until `PWIKI_INTERNAL_API_TOKEN` is set, and every request is
checked against `PWIKI_INTERNAL_API_ALLOWED_CIDRS` (default: loopback plus
RFC1918).

Quick smoke test:

```bash
curl -H "Authorization: Bearer $PWIKI_INTERNAL_API_TOKEN" \
  http://127.0.0.1:5000/api/internal/health

curl -H "Authorization: Bearer $PWIKI_INTERNAL_API_TOKEN" \
  "http://127.0.0.1:5000/api/internal/search?q=oauth&limit=10"

curl -H "Authorization: Bearer $PWIKI_INTERNAL_API_TOKEN" \
  "http://127.0.0.1:5000/api/internal/page?path=Projects/pwiki.md"

curl -H "Authorization: Bearer $PWIKI_INTERNAL_API_TOKEN" \
  "http://127.0.0.1:5000/api/internal/folder?path=Projects&recursive=false"
```

Safety guidelines:

- Do **not** publish `/api/internal/*` through your public reverse proxy. Bind
  the API consumer to localhost or a private network.
- Set `PWIKI_INTERNAL_API_TRUSTED_PROXY_CIDRS` only if a known reverse proxy
  fronts the API and you want `X-Forwarded-For` honored. Without it the API
  trusts only `request.remote_addr`, preventing header spoofing.
- The API is read-only by contract — every handler is `GET`-only and rejects
  any path that escapes the vault via `..` or a symlink target.

## 9. Git Sync

pwiki can inspect status, run sync, commit exposed-scope changes, and optionally
push. Git history remains the history for your Markdown vault.

Manual sync:

```bash
docker compose exec -T pwiki sh -lc 'python -m pwiki.cli vault sync "$PWIKI_MARKDOWN_DIR"'
```

Manual status:

```bash
docker compose exec -T pwiki sh -lc 'python -m pwiki.cli vault git-status "$PWIKI_MARKDOWN_DIR"'
```

Manual commit:

```bash
docker compose exec -T pwiki sh -lc 'python -m pwiki.cli vault commit "$PWIKI_MARKDOWN_DIR" --page index.md --author-email you@example.com --push'
```

Operational policy:

- Automatic pull is handled outside the Flask app.
- Use a systemd timer, cron, or another scheduler to run `vault sync`.
- `vault sync` requires a clean working tree and uses `git pull --ff-only`.
- `vault commit` stages only the exposed Markdown scope.
- Web saves are blocked during merge, rebase, or conflict states.

### systemd Timer

Example units live in:

```text
deploy/systemd/
```

For system units, adjust `WorkingDirectory` if your deployment is not
`/srv/newwiki`, then install:

```bash
sudo cp deploy/systemd/pwiki-vault-sync.service.example /etc/systemd/system/pwiki-vault-sync.service
sudo cp deploy/systemd/pwiki-vault-sync.timer.example /etc/systemd/system/pwiki-vault-sync.timer
sudo systemctl daemon-reload
sudo systemctl enable --now pwiki-vault-sync.timer
systemctl list-timers pwiki-vault-sync.timer
```

For user units:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/user-pwiki-vault-sync.service.example ~/.config/systemd/user/pwiki-vault-sync.service
cp deploy/systemd/user-pwiki-vault-sync.timer.example ~/.config/systemd/user/pwiki-vault-sync.timer
systemctl --user daemon-reload
systemctl --user enable --now pwiki-vault-sync.timer
systemctl --user list-timers pwiki-vault-sync.timer
```

Enable linger if the user timer must run without an active login session:

```bash
loginctl enable-linger "$USER"
```

## 10. nginx Reverse Proxy

For root-path deployment:

```nginx
location / {
    proxy_pass         http://127.0.0.1:5000/;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Real-IP         $remote_addr;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
}
```

For `/newwiki` sub-path deployment:

```nginx
location /newwiki/ {
    proxy_pass         http://127.0.0.1:5000/;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Real-IP         $remote_addr;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
}

location = /newwiki {
    return 301 /newwiki/;
}
```

Use:

```env
PWIKI_PUBLIC_BASE_URL=https://wiki.example.com
PWIKI_URL_PREFIX=/newwiki
```

Apply changes:

```bash
nginx -t && nginx -s reload
docker compose up -d
```

## 11. Local Run Without Docker

From the repository root:

```bash
pip install -r pwiki/requirements.txt
PWIKI_MARKDOWN_DIR=./your-vault PWIKI_ALLOW_ANONYMOUS=1 python pwiki/app.py
```

For local OAuth smoke testing:

```bash
mkdir -p /tmp/pwiki-vault /tmp/pwiki-data
cd pwiki
PYENV_VERSION=v3.13 \
PWIKI_MARKDOWN_DIR=/tmp/pwiki-vault \
PWIKI_DATA_DIR=/tmp/pwiki-data \
PWIKI_DB_PATH=/tmp/pwiki-data/pwiki.db \
PWIKI_READ_ONLY=0 \
PWIKI_SECRET_KEY=local-dev-secret-change-me \
GOOGLE_OAUTH_CLIENT_ID=... \
GOOGLE_OAUTH_CLIENT_SECRET=... \
PWIKI_ADMIN_GOOGLE_EMAIL=you@example.com \
FLASK_PORT=5000 \
python app.py
```

Register this redirect URI in Google Cloud Console:

```text
http://localhost:5000/auth/google/callback
```

Leave `PWIKI_PUBLIC_BASE_URL` empty for direct localhost testing.

## 12. Backup

Back up:

- the Obsidian vault or its Git remote
- `volumes/pwiki-data/`, especially `pwiki.db`
- uploaded files if you use uploads

SQLite backup:

```bash
mkdir -p backup
docker compose exec pwiki sh -lc 'sqlite3 "$PWIKI_DB_PATH" ".backup /tmp/pwiki.db"'
docker compose cp pwiki:/tmp/pwiki.db ./backup/pwiki.db
```

Or stop the container and copy `volumes/pwiki-data/pwiki.db` directly.

## 13. Troubleshooting

### Container Does Not Start

```bash
docker compose logs pwiki
```

Common causes:

- missing `PWIKI_SECRET_KEY`
- OAuth variables partly configured
- OAuth disabled without `PWIKI_ALLOW_ANONYMOUS=1`
- `PWIKI_MARKDOWN_SUBDIR` resolving outside or missing under `PWIKI_GIT_HOST_DIR`

### Sidebar Is Empty

Check the host path:

```bash
find "${PWIKI_GIT_HOST_DIR}/${PWIKI_MARKDOWN_SUBDIR}" -name '*.md' | head -20
```

Check the container path:

```bash
docker compose exec pwiki sh -lc 'echo "$PWIKI_MARKDOWN_DIR"; find "$PWIKI_MARKDOWN_DIR" -name "*.md" | head -20'
```

Enable diagnostic logging:

```env
PWIKI_FILE_IO_LOG=1
```

then recreate the container and inspect:

```bash
docker compose logs -f pwiki
```

If logs show `markdown scan root='/data/git/...' pages=0`, the app scanned that
container path successfully but found no Markdown files there. Re-check
`PWIKI_GIT_HOST_DIR` and `PWIKI_MARKDOWN_SUBDIR`.

### Admin Account Is Lost

Grant an admin user through the CLI:

```bash
docker compose exec pwiki sh -lc 'python -m pwiki.cli users grant you@example.com --admin --default-permission write'
```

Or update `PWIKI_ADMIN_GOOGLE_EMAIL` in `.env` and restart. Seeding is
idempotent.

### SVG Attachments Do Not Render

SVG is intentionally excluded from inline rendering because SVG can contain
script. Convert diagrams to PNG/JPG, or add a sanitizer before re-enabling SVG.
