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
docker compose run --rm pwiki python -m cli config show
docker compose run --rm pwiki python -m cli vault git-status "$PWIKI_MARKDOWN_DIR"
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
| `PWIKI_GIT_AUTO_COMMIT` | `1` makes the **container** commit and push after web saves/deletes/uploads. Only works when the container can reach the remote (token remote or mounted SSH key); on the standard host-owns-Git deployment leave this `0` and use `PWIKI_GIT_HOST_PUSH`. |
| `PWIKI_GIT_AUTO_REBASE` | `1` rebases onto the upstream tip before a container auto-commit push so a stale-base web edit fast-forwards instead of diverging; a real conflict aborts the rebase and keeps the commit unpushed. (Only with `PWIKI_GIT_AUTO_COMMIT=1`.) |
| `PWIKI_GIT_HOST_PUSH` | Read by the host sync script (`deploy/pwiki-vault-sync.sh`). `1` = bidirectional (commit local web writes on the host, rebase onto remote, push). Unset/`0` = pull-only (read-only server). |
| `PWIKI_GIT_AUTHOR_NAME` / `PWIKI_GIT_AUTHOR_EMAIL` | Optional commit identity for host-side bidirectional commits. Default `pwiki` / `pwiki@localhost`. |
| `PWIKI_ATTACHMENT_SUBDIR` | Vault-relative folder web uploads land in (embedded as `![[<subdir>/<file>]]`); kept inside the vault so uploads sync via Git. Defaults to `attachments`. |
| `PWIKI_VAULT_MOUNT_MODE` | Compose volume mode for the vault, usually `rw` or `ro`. |
| `PWIKI_UID` / `PWIKI_GID` | UID/GID the container runs as (compose `user:`). Set to the host login user's `id -u` / `id -g` so container-written vault files are host-owned. Required for bidirectional sync; defaults to `1000`. |
| `PWIKI_LOG_FILE` | Optional rotating log file path, for example `/data/pwiki/pwiki.log`. |
| `PWIKI_MAX_CONTENT_LENGTH` | Flask request body limit in bytes (also the per-upload size cap). Default is 5 MiB. |
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
docker compose exec pwiki sh -lc 'python -m cli users list'
docker compose exec pwiki sh -lc 'python -m cli users grant alice@example.com --default-permission read'
docker compose exec pwiki sh -lc 'python -m cli users grant admin2@example.com --admin --default-permission write'
docker compose exec pwiki sh -lc 'python -m cli users path-grant alice@example.com Shared/Family read'
docker compose exec pwiki sh -lc 'python -m cli users path-grant alice@example.com Private none'
docker compose exec pwiki sh -lc 'python -m cli users show alice@example.com'
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

pwiki reads Markdown from disk, so syncing the vault is just a Git pull of the
host working tree. **Run the pull on the host, not inside the container.** The
container image ships without an SSH client or keys, so it cannot reach an SSH
Git remote; your host login user already has the `~/.ssh` credentials. The vault
`.git` is the same directory the container mounts (`PWIKI_GIT_HOST_DIR` →
`/data/git`), so a host-side pull is immediately visible to pwiki with no
restart.

Manual sync (on the host):

```bash
# from the repo root; PWIKI_GIT_HOST_DIR comes from the deployment .env
set -a; . ./.env; set +a
git -C "$PWIKI_GIT_HOST_DIR" pull --ff-only
```

Manual status:

```bash
git -C "$PWIKI_GIT_HOST_DIR" fetch && git -C "$PWIKI_GIT_HOST_DIR" status -sb
```

Inspection helpers (read-only) can still run inside the container, which has the
`git` binary:

```bash
docker compose exec -T pwiki sh -lc 'python -m cli vault git-status "$PWIKI_MARKDOWN_DIR"'
```

> Note the in-container CLI is `python -m cli` (the image flattens the package to
> top-level modules at `/app`), not `python -m pwiki.cli`. The `pwiki.cli` form
> only works in the source repo where `pwiki/` is an importable package.

Operational policy:

- All Git network operations happen **on the host**, not in the Flask app or the
  container — `deploy/pwiki-vault-sync.sh` (invoked by the systemd timer) owns
  them, because only the host has the SSH keys.
- The script has **two modes**, chosen by `PWIKI_GIT_HOST_PUSH` in `.env`:
  - **Pull-only (default, `unset`/`0`)** — for a read-only server. Runs
    `git pull --ff-only`, which refuses to run on a diverged/dirty tree, so an
    unexpected local change surfaces as a failure instead of an automatic merge.
    Keep the server pull-only with `PWIKI_READ_ONLY=1` and
    `PWIKI_GIT_AUTO_COMMIT=0`.
  - **Bidirectional (`PWIKI_GIT_HOST_PUSH=1`)** — for a server where web
    edits/uploads are allowed (`PWIKI_READ_ONLY=0`). The container writes files
    only (keep `PWIKI_GIT_AUTO_COMMIT=0`), and the host script commits those
    writes, `rebase`s onto the remote to absorb Obsidian-side pushes, then
    pushes. A true content conflict aborts the rebase, keeps the commit
    unpushed, and logs a message for manual resolution. This is the recommended
    way to publish web writes.
- **Why not `PWIKI_GIT_AUTO_COMMIT=1`?** That makes the *container* commit and
  push, but the default image has no SSH client/keys, so the push fails and the
  commits/dirty tree pile up — which then also blocks the host pull. Only use
  `PWIKI_GIT_AUTO_COMMIT=1` if you have deliberately given the container push
  access (HTTPS-token remote or a mounted SSH key); otherwise leave it `0` and
  use `PWIKI_GIT_HOST_PUSH=1`.
- The most common breakage: `PWIKI_READ_ONLY=0` with **both**
  `PWIKI_GIT_AUTO_COMMIT=0` and `PWIKI_GIT_HOST_PUSH` unset. Uploads then land on
  disk (and display) but are never committed, leaving a dirty tree that stops the
  host pull entirely. Fix by setting `PWIKI_GIT_HOST_PUSH=1`.

#### Container UID must match the host user (every deployment)

`docker-compose.yml` runs the container as a non-root user
(`user: "${PWIKI_UID:-1000}:${PWIKI_GID:-1000}"`). That UID has to own two things
the container writes:

1. **`volumes/pwiki-data/`** — `pwiki.db` (users, ACL, sessions) and logs. The app
   writes this on **every** deployment, including read-only ones. A previous
   **root** container left these files `root:root`; after the switch the non-root
   process can no longer write them and the app fails to start.
2. **The vault bind-mount** — only when bidirectional sync is on. The container
   writes web edits/uploads, and the host `systemctl --user` timer later
   `git add`s them. If they are `root:root`, the timer (your login user) cannot
   stage them:

   ```text
   error: open("…/index.md"): Permission denied
   fatal: adding files failed
   pwiki-vault-sync.service: Main process exited, code=exited, status=128
   ```

Fix: set the container UID to your host login user, then reclaim any files an
earlier root container already wrote.

```bash
echo "PWIKI_UID=$(id -u)" >> .env
echo "PWIKI_GID=$(id -g)" >> .env
# One-time ownership reclaim — BOTH the vault and the data volume:
sudo chown -R "$(id -u):$(id -g)" "$PWIKI_GIT_HOST_DIR" ./volumes/pwiki-data
```

> Run `install.py` **as your login user, not under `sudo`** — it reads the
> effective UID to verify the match, and `sudo` (euid 0) would mis-detect it as
> root. It warns when the container UID will not be able to write
> `volumes/pwiki-data`, and hard-fails when `PWIKI_GIT_HOST_PUSH=1` and the UID
> does not match the host user. `PWIKI_UID` must be **numeric** — compose
> substitutes it verbatim into `user:`, so a name fails at container start.

### systemd Timer

Example units live in:

```text
deploy/systemd/
```

The service unit reads `PWIKI_GIT_HOST_DIR` (and `PWIKI_GIT_HOST_PUSH`) from the
deployment `.env` via `EnvironmentFile=` and runs `deploy/pwiki-vault-sync.sh` on
the host — pull-only by default, bidirectional when `PWIKI_GIT_HOST_PUSH=1`. The
example files use a `/srv/newwiki` placeholder for `WorkingDirectory`,
`EnvironmentFile`, and the script path; adjust them if the repository lives
elsewhere. (`./install.py` rewrites these to the actual repo path automatically
when it installs the **user** units, and validates that the script is present and
executable.)

**Prefer the user units.** They run as your login user, so the host `~/.ssh`
keys reach an SSH Git remote without extra configuration. A **system** unit runs
as root, which usually lacks those keys — uncomment `User=`/`Group=` in the
service file to run it as an account that can reach the remote.

For user units (the easiest path is `./install.py`, which writes these with the
correct repo path already; the manual steps below substitute `/srv/newwiki`
yourself):

```bash
mkdir -p ~/.config/systemd/user
# Replace /srv/newwiki with the absolute path to this repo so EnvironmentFile
# points at the real .env.
sed "s#/srv/newwiki#$PWD#g" deploy/systemd/user-pwiki-vault-sync.service.example \
  > ~/.config/systemd/user/pwiki-vault-sync.service
cp deploy/systemd/user-pwiki-vault-sync.timer.example ~/.config/systemd/user/pwiki-vault-sync.timer
systemctl --user daemon-reload
systemctl --user enable --now pwiki-vault-sync.timer
systemctl --user list-timers pwiki-vault-sync.timer
```

Enable linger if the user timer must run without an active login session:

```bash
loginctl enable-linger "$USER"
```

For system units (root) instead, edit `User=`/`Group=` and the `/srv/newwiki`
paths in the service file first, then:

```bash
sudo cp deploy/systemd/pwiki-vault-sync.service.example /etc/systemd/system/pwiki-vault-sync.service
sudo cp deploy/systemd/pwiki-vault-sync.timer.example /etc/systemd/system/pwiki-vault-sync.timer
sudo systemctl daemon-reload
sudo systemctl enable --now pwiki-vault-sync.timer
systemctl list-timers pwiki-vault-sync.timer
```

### Checking sync health

Verify sync in three layers — **is the timer scheduled → did the last run
succeed → does the content actually match the remote**. Commands below assume the
**user** units; for system units drop `--user` and prefix `journalctl` with
`sudo`. Run as your login user, not `sudo`.

**1. Timer is alive and scheduled**

```bash
systemctl --user list-timers pwiki-vault-sync.timer   # NEXT / LAST fire time
systemctl --user is-enabled pwiki-vault-sync.timer    # -> enabled
systemctl --user is-active  pwiki-vault-sync.timer    # -> active (waiting)
loginctl show-user "$USER" -p Linger                  # -> Linger=yes
```

`Linger=no` means the timer stops when you log out — fix with
`loginctl enable-linger "$USER"`.

**2. The last run succeeded** (the most useful check)

```bash
systemctl --user status pwiki-vault-sync.service              # last result / exit code
journalctl --user -u pwiki-vault-sync.service -n 40 --no-pager
journalctl --user -u pwiki-vault-sync.service -f             # follow live
```

Reading the log:

- **OK** — quiet exit, a pull summary, or `Everything up-to-date`; `status=0/SUCCESS`.
- **Conflict (bidirectional)** — `rebase conflict on '<branch>'; resolve manually
  in the vault.` then exit 1. The local commit is preserved and unpushed; resolve
  in Obsidian.
- **Permission denied** — `error: open(...): Permission denied` /
  `fatal: adding files failed`. The container UID still does not match the host
  user — see *Container UID must match the host user* above (set `PWIKI_UID` and
  run the one-time `chown`).

**3. Run it once now**

```bash
systemctl --user start pwiki-vault-sync.service
journalctl --user -u pwiki-vault-sync.service -n 20 --no-pager
```

**4. Content actually matches the remote** (inspect the vault repo directly)

```bash
cd "$PWIKI_GIT_HOST_DIR"        # set -a; source /srv/newwiki/.env; set +a to get it
git status                      # clean = good; dirty = uncommitted web writes
git fetch --quiet
git log --oneline @{u}..        # local-only commits not yet pushed (ahead)
git log --oneline ..@{u}        # remote-only commits not yet pulled (behind)
```

Both `git log` ranges empty = fully in sync. On a pull-only server, a dirty tree
or any ahead commits mean `git pull --ff-only` is refusing to advance (typically a
root container committing web writes) and sync is stuck until that is cleared.

Day-to-day, layers 1–2 (`list-timers` + `journalctl … -n 20`) are enough; use
layer 4 when you need to confirm the trees are identical.

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
uv sync
PWIKI_MARKDOWN_DIR=./your-vault PWIKI_ALLOW_ANONYMOUS=1 uv run python pwiki/app.py
```

For local OAuth smoke testing:

```bash
mkdir -p /tmp/pwiki-vault /tmp/pwiki-data
cd pwiki
PWIKI_MARKDOWN_DIR=/tmp/pwiki-vault \
PWIKI_DATA_DIR=/tmp/pwiki-data \
PWIKI_DB_PATH=/tmp/pwiki-data/pwiki.db \
PWIKI_READ_ONLY=0 \
PWIKI_SECRET_KEY=local-dev-secret-change-me \
GOOGLE_OAUTH_CLIENT_ID=... \
GOOGLE_OAUTH_CLIENT_SECRET=... \
PWIKI_ADMIN_GOOGLE_EMAIL=you@example.com \
FLASK_PORT=5000 \
uv run python app.py
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
docker compose exec pwiki sh -lc 'python -m cli users grant you@example.com --admin --default-permission write'
```

Or update `PWIKI_ADMIN_GOOGLE_EMAIL` in `.env` and restart. Seeding is
idempotent.

### SVG Attachments Do Not Render

SVG is intentionally excluded from inline rendering because SVG can contain
script. Convert diagrams to PNG/JPG, or add a sanitizer before re-enabling SVG.
