# pwiki

> Read your Obsidian vault from a browser, and share only the folders you want.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Status: v0.1 early release](https://img.shields.io/badge/status-v0.1%20early%20release-orange.svg)

English | [한국어](README.ko.md)

A Flask-based Markdown wiki for reading an Obsidian vault on the web, with optional restricted editing.

pwiki is designed for people who want to keep Obsidian as the source of truth for their personal knowledge base (LLM wiki), while still being able to browse selected notes through a web browser or share only specific folders with other people.

## Why?

Obsidian is excellent as a local-first writing and knowledge-management tool. A web interface becomes useful when you want to:

- quickly check notes from work or outside your home network
- share only part of your Obsidian vault with family members or colleagues
- keep growing a personal knowledge base (LLM wiki) with Obsidian + an LLM plugin, then read and search it from any browser — no Obsidian client, no model tokens
- host a personal wiki on a home server, NAS, or Synology box behind a home router, then expose it through a reverse proxy or tunnel
- run the web UI mostly in read-only mode, but occasionally make small edits and sync them back through Obsidian/Git

pwiki is not an Obsidian replacement. It is a lightweight web layer that works directly with the Markdown files already managed by Obsidian. It does not import notes into a separate database or convert them into another format.

Read-only is the default, and write access can be opened only for selected users and paths. This makes it practical to keep personal and shared notes in the same vault while exposing only the folders you actually want to share.

Obsidian remains the primary editing tool, while pwiki handles remote browsing, selective sharing, and the occasional web edit. The scope is deliberately small — see *What pwiki is NOT* below for what's out of scope.

## Features

**Reading & rendering**

- Reads Markdown files directly from an Obsidian vault — no separate database, no format conversion
- Can point at your real Obsidian vault root; `.obsidian/`, `.git/`, and other dotfiles are ignored automatically
- Renders Obsidian-style wikilinks, attachments, tags, and callouts
- Simple file-based search (no external index server)
- Mobile read-only browsing UI

**Sharing & permissions**

- Google OAuth login with per-user and per-path permissions
- User management via CLI and web admin UI
- Unauthorized pages are hidden from the sidebar, mobile drawer, search results, and index listing

**Editing & Git**

- Read-only by default; web editing is opt-in per user and path
- Sync / status / commit / push for Git-managed vaults
- Conflict detection on save; atomic writes preserve existing newline style

**Programmatic access**

- Optional read-only JSON API at `/api/internal/*` for trusted same-host or private-network consumers (a local AI assistant, RAG pipeline, backup script, etc.)
- Bearer-token + CIDR-allowlist authentication that lives entirely outside the OAuth web flow
- Read-only by contract — only `GET` handlers are registered; off by default until you set a token

**Operations**

- Reverse-proxy sub-path deployment support
- Installer + deployment helper via `install.sh` / `install.py`
- Docker Compose ready

## What pwiki is NOT

A few things pwiki intentionally does not try to be:

- It is not an Obsidian replacement. Obsidian stays the primary editor, and pwiki is a thin web layer over the same Markdown files.
- It is not a large team wiki. The target is personal or small vaults — roughly under a thousand documents — and search just reads files directly instead of running a separate index.
- It is not a mobile editor. The mobile view is read-only on purpose.

## Use Cases

### 1. Browse Your Obsidian Notes Remotely

Point pwiki at an Obsidian vault on a home PC or NAS, then browse your Markdown notes from a web browser.

This is useful for quickly checking development notes, setup instructions, purchase records, household information, or other personal references while away from your main machine.

### 2. Share Only Selected Folders

You can avoid exposing the whole vault and grant read access only to specific folders or file paths.

Examples:

- `Family/Travel`
- `Shared/Home Repair`
- `Work Share/Project A`
- `Notes/Development`

Private journals and sensitive notes can stay hidden.

### 3. Make Small Web Edits and Sync Back to Obsidian

Read-only mode is recommended as the default operating mode.

When needed, set `PWIKI_READ_ONLY=0` and grant write permission to selected users. With a vault managed through Git, web saves can be committed and pushed, then pulled back into the Obsidian environment through your normal Git sync workflow.

### 4. Deploy on a Home Server / NAS / Synology

pwiki supports Docker Compose deployments.

You can run it on a home server, NAS, or Synology-style machine behind a home router, then expose it externally through nginx reverse proxy, Cloudflare Tunnel, Tailscale, ngrok, or a similar access layer.

For external access, HTTPS, OAuth, and a strong `PWIKI_SECRET_KEY` are strongly recommended.

### 5. Point pwiki at your real Obsidian vault

If access control isn't a concern for your setup, you can simply point pwiki at the same folder Obsidian itself uses. pwiki ignores `.obsidian/`, `.git/`, and other dotfiles when scanning, so Obsidian's config and plugins stay invisible from the web side, and only your `.md` files are surfaced.

One caveat: avoid letting Obsidian and pwiki write to the same file at the same time. The safest patterns are read-only pwiki next to a live Obsidian vault, or write-mode pwiki on a Git checkout that you sync into the vault through Obsidian's Git plugin.

### 6. Browse notes built up with Obsidian's LLM plugins

If you've been using Obsidian together with an LLM plugin (Smart Connections, Copilot, Text Generator, and similar) to grow a personal knowledge base (LLM wiki), the notes themselves are still just `.md` files. Point pwiki at the same vault and you can read or search those notes from any browser — no Obsidian client to install, and no model tokens spent on the lookup. The vault keeps growing through Obsidian + the LLM; pwiki simply serves the result.

## Mobile Support

On mobile, pwiki keeps things simple. The sidebar becomes a folder drawer, the layout is tuned for reading on small screens, and the editor isn't exposed on purpose.

The project intentionally stays minimal: Flask, Jinja2, and a small amount of vanilla JavaScript. Building a reliable mobile Markdown editor with preview, conflict handling, and file management would add a lot of frontend complexity, so mobile stays focused on reading while editing happens from desktop web or through the Obsidian/Git sync workflow.

## Requirements

- Python 3.11 or newer. The lockfile and the project's `PYENV_VERSION` target use 3.13, so that's the version that gets the most testing.
- Docker and Docker Compose for the standard deployment path.
- An Obsidian vault on disk. If you want sync to work smoothly, manage the vault with Git.
- A Linux or macOS host. Other Unix-like systems should also work; Windows is untested.

## Quick Start

### 1. Clone

```bash
git clone https://github.com/bongdang/pwiki.git
cd pwiki
```

### 2. Configure

```bash
cp .env.example .env
$EDITOR .env
```

At a minimum, fill in:

- `PWIKI_SECRET_KEY` — a long random string. The example file shows a one-liner to generate one.
- `PWIKI_GIT_HOST_DIR` — the host path to your Obsidian vault (its Git working tree).
- `PWIKI_MARKDOWN_SUBDIR` — a sub-folder to expose, or leave it empty to share the whole vault.

If you want Google login, also set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `PWIKI_ADMIN_GOOGLE_EMAIL`. If you'd rather skip OAuth entirely, set `PWIKI_ALLOW_ANONYMOUS=1` to opt in to anonymous read-only mode. Without that flag startup refuses to boot, so an unconfigured `.env` cannot silently expose the vault.

### 3. Run the installer

```bash
./install.sh
```

The installer walks through `.env`, double-checks OAuth / read-only / Git settings, runs Docker Compose, prints sync timer and reverse-proxy guidance, and shows you the Google OAuth redirect URI to register.

## Important Environment Variables

The full annotated list lives in `.env.example`. The ones you tend to touch most often:

| Name | Description |
|---|---|
| `PWIKI_SECRET_KEY` | Long random string used to protect login sessions (required in production) |
| `PWIKI_GIT_HOST_DIR` | Host path to the Obsidian vault Git working tree (mounted into the container) |
| `PWIKI_MARKDOWN_SUBDIR` | Sub-folder of the vault to expose; empty = whole vault |
| `PWIKI_READ_ONLY` | `1` blocks all web writes (default) |
| `PWIKI_ALLOW_ANONYMOUS` | `1` opts in to OAuth-less anonymous read-only mode |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client id |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret |
| `PWIKI_ADMIN_GOOGLE_EMAIL` | Google email seeded as the first admin |
| `PWIKI_PUBLIC_BASE_URL` | External base URL (used to build the OAuth redirect URI behind a proxy) |
| `PWIKI_URL_PREFIX` | URL path such as `/newwiki` when serving under a sub-path |
| `PWIKI_INTERNAL_API_TOKEN` | Bearer token that enables `/api/internal/*`. Empty disables the whole local API. |
| `PWIKI_INTERNAL_API_ALLOWED_CIDRS` | CIDR allowlist for the local API. Defaults to loopback + RFC1918. |
| `PWIKI_INTERNAL_API_TRUSTED_PROXY_CIDRS` | CIDRs whose `X-Forwarded-For` may be honored. Empty = always use `remote_addr`. |

## Permission Model

Authentication is Google OAuth — that's the only login backend. Admins grant access through either the CLI or the web admin UI.

Permission values:

- `none`
- `read`
- `write`

Permissions are resolved from a per-user default plus path-based exceptions.

For example, a user can have default `none` permission while receiving `read` access to `Shared/Family`.

Permissions can be managed through both the CLI and the web admin UI.

```bash
python -m pwiki.cli users grant alice@example.com --default-permission read
python -m pwiki.cli users path-grant alice@example.com Shared/Family read
python -m pwiki.cli users path-grant alice@example.com Private none
python -m pwiki.cli users show alice@example.com
```

Admin users can perform the same kind of management from the web admin UI.

Unauthorized pages are not only blocked on direct access; they are also hidden from the sidebar, mobile drawer, search results, and index listing. This makes it practical to keep one vault while sharing only selected folders with family members or colleagues.

## Local Read-Only API

Alongside the OAuth-protected web UI, pwiki ships a separate read-only JSON API at `/api/internal/*` that is designed for *trusted* same-host or private-network consumers — a local AI assistant, a RAG pipeline, a backup or indexing script, etc. The surface is disabled until you set `PWIKI_INTERNAL_API_TOKEN`, so an unconfigured deployment exposes nothing.

Why a separate API instead of just calling the web UI?

- **No OAuth round-trip.** A background process or local AI agent does not have a browser or a Google session. The API uses a single Bearer token instead, so calling it from a script or scheduler is trivial.
- **Read-only by contract.** Only `GET` handlers are registered; no save, delete, or upload path exists on this surface. You can hand the token to a local script without worrying about accidental writes.
- **Defense in depth.** Every request is checked against a CIDR allowlist (default: loopback + RFC1918) *before* the token is even compared, and `X-Forwarded-For` is ignored unless you explicitly opt in via `PWIKI_INTERNAL_API_TRUSTED_PROXY_CIDRS`. A leaked token alone is not enough to read from outside your private network.
- **Path-safe.** Every `path` argument is resolved with `os.path.realpath` against the vault root, so `..` traversal and symlinks pointing outside the vault are rejected with `403 forbidden_path`.
- **Always fresh.** Each request walks the filesystem directly — no in-process cache. Deleting or editing a file on disk shows up on the next call, no server restart required.
- **Same vault, no duplication.** The API reads exactly the same Markdown files the web UI does. There is no separate index to keep in sync.

### Why not just MCP?

A fair question — if you already have an MCP-capable client like Claude Desktop, why not expose the vault through an MCP server and let the model query it directly?

The two approaches optimize for different workloads. **MCP makes the LLM the orchestrator**: every `search` → `read` → `search` round-trip is another tool result accumulating in the model's context, plus the tool definitions sitting in the system prompt. That's a great fit for free-form conversational exploration ("find anything about X and summarize"), but it gets expensive when you want to do the same thing on a schedule, or run it dozens of times a day.

**The internal API lets *you* be the orchestrator**, which moves filtering, ranking, deduplication, and chunking out of the LLM's context entirely. A local RAG pipeline can embed the whole vault with a local model, look up the top-k chunks via this API, and send only the relevant snippets to the LLM — often a single round-trip per question. Non-LLM consumers (backup scripts, daily-digest cron jobs, local indexers) spend zero tokens at all. The read-only constraint isn't a weakness here; most of these workloads never need to write back, and dropping the write surface keeps the threat model small.

The two are complementary, not competing. For a chat-style "ask my notes anything" experience, MCP is the cleaner fit. For repeatable, schedulable, token-frugal automation against the same vault, this API is the cleaner fit — and nothing stops you from running both side by side.

Endpoints:

| Method & path | Purpose |
|---|---|
| `GET /api/internal/health` | Service liveness — confirms the API is enabled and you passed both guards. |
| `GET /api/internal/search?q=<query>&limit=<1..100>` | Case-insensitive filename + body search (default `limit=20`). Snippets are localized around the match. |
| `GET /api/internal/page?path=<vault-relative .md path>` | Returns full Markdown content + title + mtime for one page. |
| `GET /api/internal/folder?path=<folder>&recursive=<bool>&limit=<1..500>` | Lists `.md` files and sub-folders. Hidden entries, `.git`, and non-Markdown attachments are filtered. |

The error envelope is the same everywhere:

```json
{ "error": { "code": "unauthorized", "message": "Invalid internal API token." } }
```

Quick smoke test:

```bash
export PWIKI_INTERNAL_API_TOKEN='a-long-random-token'

curl -H "Authorization: Bearer $PWIKI_INTERNAL_API_TOKEN" \
  http://127.0.0.1:5000/api/internal/health

curl -H "Authorization: Bearer $PWIKI_INTERNAL_API_TOKEN" \
  "http://127.0.0.1:5000/api/internal/search?q=oauth&limit=10"
```

**Important:** do *not* expose `/api/internal/*` through your public reverse proxy. The API is meant to be reached on `localhost` or a private network. If your nginx config publishes everything under `/`, add an explicit `location /api/internal/ { return 404; }` block, or bind the container to `127.0.0.1` in `docker-compose.yml`.

## Security Defaults

The defaults lean conservative because pwiki is meant for personal vaults.

- Read-only is the default.
- Anonymous mode without OAuth must be explicitly enabled.
- Write access must pass both the global read-only setting and per-user permissions.
- Web form submissions are protected.
- SVG attachments are not rendered by default for security reasons.
- Startup refuses the default development key when OAuth and web writes are both enabled.
- HTTPS and a long random `PWIKI_SECRET_KEY` are recommended for external deployments.

## Git & Obsidian sync

Because pwiki reads and writes Markdown files directly, it pairs naturally with a Git-managed vault. A common setup is to use a Git plugin inside Obsidian to sync the vault with a remote Git repository, and point the pwiki server at the same local Git folder.

For Git-managed vaults, pwiki can:

- inspect vault status
- run a sync helper
- commit after web saves
- optionally push
- block web saves while Git is in a conflict or merge state

Automatic sync works best when paired with an operator-managed timer or another explicit sync workflow.

References:

- Obsidian Git plugin: https://github.com/Vinzent03/obsidian-git
- Obsidian community plugins: https://help.obsidian.md/community-plugins

A few things to keep in mind:

- Don't put a private vault in a public repository.
- Use a private Git repository or a self-hosted Git server for sensitive notes.
- Before turning on pwiki's web-save auto commit/push, double-check your Git remote permissions and conflict-handling workflow.

## Local Run

To try pwiki locally without Docker, install the Python dependencies and run the app from the repository root:

```bash
pip install -r pwiki/requirements.txt
PWIKI_MARKDOWN_DIR=./your-vault PWIKI_ALLOW_ANONYMOUS=1 python pwiki/app.py
```

There are three requirements files in the repo, each for a different job:

| File | What it's for |
|---|---|
| `pwiki/requirements.txt` | The everyday development install, with loose version pins. |
| `pwiki/requirements.lock.txt` | The reproducible runtime install — this is what the Docker image uses. |
| `install-requirements.txt` | Reserved for `install.sh`'s own dependencies. It's empty right now because the installer only uses Python's standard library. |

For the full deployment procedure see [`deploy.md`](deploy.md).

## License

MIT License — see [`LICENSE`](LICENSE).
