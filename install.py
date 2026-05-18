#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parent
USER_SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"
TEMPLATE_DIR = REPO_ROOT / "deploy" / "systemd"
USER_SERVICE_TEMPLATE = TEMPLATE_DIR / "user-pwiki-vault-sync.service.example"
USER_TIMER_TEMPLATE = TEMPLATE_DIR / "user-pwiki-vault-sync.timer.example"
USER_SERVICE_TARGET = USER_SYSTEMD_DIR / "pwiki-vault-sync.service"
USER_TIMER_TARGET = USER_SYSTEMD_DIR / "pwiki-vault-sync.timer"

SECRET_KEYS = {
    "PWIKI_SECRET_KEY",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_CLIENT_ID",
}


def main() -> int:
    args = parse_args()
    env_path = REPO_ROOT / ".env"
    env = load_dotenv(env_path)
    updates = onboarding_updates(args, env)
    if updates:
        print_updates(updates)
        if args.yes or confirm("\nWrite these settings to .env?"):
            update_dotenv(env_path, updates)
            env = load_dotenv(env_path)
        else:
            return 1

    # Runtime dotenv loading lets already-exported shell environment variables
    # override file values. The installer must use the same rule so validation
    # matches what the container will actually see.
    merged_env = dict(env)
    merged_env.update(os.environ)

    problems, warnings = validate_env(env, merged_env, args)
    # Print merged_env, the effective shell-wins view, so the summary and
    # validation results are based on the same values the app will see.
    print_summary(env_path, merged_env, problems, warnings)
    print_proxy_and_oauth_guidance(merged_env)
    if problems:
        print("\nRefusing to install until the required settings are fixed.", file=sys.stderr)
        return 2

    if not args.yes and not confirm("\nProceed with user systemd install and docker compose up?"):
        return 1

    use_git = truthy(merged_env.get("PWIKI_USE_GIT", "1"))
    if not args.skip_systemd and use_git:
        install_user_systemd(args.yes)
        check_user_timer()
        maybe_enable_linger(args.yes)
    elif not use_git:
        print("\nSkipping user systemd timer because PWIKI_USE_GIT=0.")

    if not args.skip_docker:
        run(["docker", "compose", "up", "-d", "--build"], cwd=REPO_ROOT, env=merged_env)
        run(["docker", "compose", "ps"], cwd=REPO_ROOT, env=merged_env, check=False)
        run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "pwiki",
                "sh",
                "-lc",
                'python -m pwiki.cli config show && python -m pwiki.cli vault git-status "$PWIKI_MARKDOWN_DIR"',
            ],
            cwd=REPO_ROOT,
            env=merged_env,
            check=False,
        )

    print("\nInstall checks completed.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate pwiki deployment env, install user systemd sync timer, and run docker compose.",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Do not prompt before applying changes")
    parser.add_argument("--skip-systemd", action="store_true", help="Do not install or start the user systemd timer")
    parser.add_argument("--skip-docker", action="store_true", help="Do not run docker compose up")
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--anonymous", action="store_true", help="Configure anonymous read-only mode")
    auth.add_argument("--oauth", action="store_true", help="Configure Google OAuth mode")
    write = parser.add_mutually_exclusive_group()
    write.add_argument("--read-only", action="store_true", help="Keep web writes disabled")
    write.add_argument("--write", action="store_true", help="Enable web writes (OAuth required)")
    git = parser.add_mutually_exclusive_group()
    git.add_argument("--git", action="store_true", help="Configure Git-backed vault operations")
    git.add_argument("--no-git", action="store_true", help="Disable Git sync/auto-commit checks in the installer")
    auto_commit = parser.add_mutually_exclusive_group()
    auto_commit.add_argument("--auto-commit", action="store_true", help="Enable web-save auto commit/push")
    auto_commit.add_argument("--no-auto-commit", action="store_true", help="Disable web-save auto commit/push")
    parser.add_argument(
        "--no-onboarding",
        action="store_true",
        help="Do not ask onboarding questions when mode options are omitted",
    )
    parser.add_argument("--nginx", action="store_true", help="Configure reverse proxy URL settings")
    parser.add_argument("--no-nginx", action="store_true", help="Clear reverse proxy URL settings")
    parser.add_argument("--public-base-url", help="External scheme+host, for example https://example.com")
    parser.add_argument("--url-prefix", help="Reverse proxy path prefix, for example /newwiki")
    return parser.parse_args()


def onboarding_updates(args: argparse.Namespace, env: dict[str, str]) -> dict[str, str]:
    explicit = any(
        (
            args.anonymous,
            args.oauth,
            args.read_only,
            args.write,
            args.git,
            args.no_git,
            args.auto_commit,
            args.no_auto_commit,
            args.nginx,
            args.no_nginx,
            args.public_base_url is not None,
            args.url_prefix is not None,
        )
    )
    interactive = not args.yes and not args.no_onboarding and sys.stdin.isatty()
    if not explicit and not interactive:
        return {}

    updates: dict[str, str] = {}

    if args.anonymous:
        auth_mode = "anonymous"
    elif args.oauth:
        auth_mode = "oauth"
    elif interactive:
        auth_mode = prompt_choice(
            "Authentication mode",
            [
                ("oauth", "Google OAuth login (recommended for write/admin mode)"),
                ("anonymous", "Anonymous read-only browsing"),
            ],
            default=current_auth_mode(env),
        )
    else:
        auth_mode = current_auth_mode(env)

    if auth_mode == "anonymous":
        updates.update(
            {
                "PWIKI_ALLOW_ANONYMOUS": "1",
                "GOOGLE_OAUTH_CLIENT_ID": "",
                "GOOGLE_OAUTH_CLIENT_SECRET": "",
                "PWIKI_ADMIN_GOOGLE_EMAIL": "",
                "PWIKI_READ_ONLY": "1",
                "PWIKI_GIT_AUTO_COMMIT": "0",
            }
        )
        read_only = True
    else:
        updates["PWIKI_ALLOW_ANONYMOUS"] = "0"
        ensure_oauth_values(env, updates, interactive)
        if args.write:
            read_only = False
        elif args.read_only:
            read_only = True
        elif interactive:
            read_choice = prompt_choice(
                "Web write mode",
                [("read-only", "Disable web writes"), ("write", "Allow web writes for users with write permission")],
                default="read-only" if truthy(env.get("PWIKI_READ_ONLY", "1")) else "write",
            )
            read_only = read_choice == "read-only"
        else:
            read_only = truthy(env.get("PWIKI_READ_ONLY", "1"))
        updates["PWIKI_READ_ONLY"] = "1" if read_only else "0"

    if args.no_git:
        use_git = False
    elif args.git:
        use_git = True
    elif interactive:
        git_choice = prompt_choice(
            "Git-backed vault",
            [("git", "Use Git sync/status for the vault"), ("no-git", "Use a plain Markdown directory")],
            default="git" if truthy(env.get("PWIKI_USE_GIT", "1")) else "no-git",
        )
        use_git = git_choice == "git"
    else:
        use_git = truthy(env.get("PWIKI_USE_GIT", "1"))

    updates["PWIKI_USE_GIT"] = "1" if use_git else "0"
    if use_git:
        ensure_git_values(env, updates, interactive)
    else:
        updates["PWIKI_GIT_AUTO_COMMIT"] = "0"

    if auth_mode == "oauth" and use_git and not read_only:
        if args.auto_commit:
            auto_commit = True
        elif args.no_auto_commit:
            auto_commit = False
        elif interactive:
            auto_choice = prompt_choice(
                "Web-save Git auto-commit",
                [("no", "Do not commit/push automatically"), ("yes", "Commit and push after successful web saves")],
                default="yes" if truthy(env.get("PWIKI_GIT_AUTO_COMMIT", "0")) else "no",
            )
            auto_commit = auto_choice == "yes"
        else:
            auto_commit = truthy(env.get("PWIKI_GIT_AUTO_COMMIT", "0"))
        updates["PWIKI_GIT_AUTO_COMMIT"] = "1" if auto_commit else "0"
        if auto_commit:
            updates["PWIKI_VAULT_MOUNT_MODE"] = "rw"
    else:
        updates["PWIKI_GIT_AUTO_COMMIT"] = "0"

    if not env.get("PWIKI_SECRET_KEY") and interactive:
        updates["PWIKI_SECRET_KEY"] = secrets.token_urlsafe(48)

    proxy_updates = proxy_onboarding_updates(args, env, interactive)
    updates.update(proxy_updates)

    return {key: value for key, value in updates.items() if env.get(key) != value}


def proxy_onboarding_updates(
    args: argparse.Namespace,
    env: dict[str, str],
    interactive: bool,
) -> dict[str, str]:
    updates: dict[str, str] = {}
    if args.no_nginx:
        updates["PWIKI_PUBLIC_BASE_URL"] = ""
        updates["PWIKI_URL_PREFIX"] = ""
        return updates

    if args.nginx or args.public_base_url is not None or args.url_prefix is not None:
        use_nginx = True
    elif interactive:
        current = "yes" if env.get("PWIKI_PUBLIC_BASE_URL") or env.get("PWIKI_URL_PREFIX") else "no"
        use_nginx = (
            prompt_choice(
                "Nginx reverse proxy",
                [("yes", "Serve pwiki behind nginx"), ("no", "Serve directly on :5000")],
                default=current,
            )
            == "yes"
        )
    else:
        return updates

    if not use_nginx:
        updates["PWIKI_PUBLIC_BASE_URL"] = ""
        updates["PWIKI_URL_PREFIX"] = ""
        return updates

    base_url = args.public_base_url if args.public_base_url is not None else env.get("PWIKI_PUBLIC_BASE_URL", "")
    url_prefix = args.url_prefix if args.url_prefix is not None else env.get("PWIKI_URL_PREFIX", "")
    if interactive:
        base_url = prompt_text(
            "Public base URL (scheme+host, no path)",
            default=base_url or "https://example.com",
        )
        url_prefix = prompt_text(
            "URL prefix (empty for root, example /newwiki)",
            default=url_prefix,
            allow_empty=True,
        )

    updates["PWIKI_PUBLIC_BASE_URL"] = normalize_base_url(base_url)
    updates["PWIKI_URL_PREFIX"] = normalize_url_prefix(url_prefix)
    return updates


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            data[key] = value
    return data


def update_dotenv(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    rewritten: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rewritten.append(raw_line)
            continue
        key_part = stripped.split("=", 1)[0].strip()
        key = key_part.removeprefix("export ").strip() if key_part.startswith("export ") else key_part
        if key in updates:
            rewritten.append(f"{key}={quote_env_value(updates[key])}")
            seen.add(key)
        else:
            rewritten.append(raw_line)

    if rewritten and rewritten[-1].strip():
        rewritten.append("")
    for key, value in updates.items():
        if key not in seen:
            rewritten.append(f"{key}={quote_env_value(value)}")

    path.write_text("\n".join(rewritten).rstrip() + "\n", encoding="utf-8")
    # This file can contain OAuth secrets and SECRET_KEY, so always lock it to 0600.
    try:
        path.chmod(0o600)
    except OSError as exc:
        print(f"warning: could not chmod 600 {path}: {exc}", file=sys.stderr)
    print(f"wrote {path}")


def quote_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(ch.isspace() for ch in value) or any(ch in value for ch in ['"', "'", "#"]):
        return shlex.quote(value)
    return value


def current_auth_mode(env: dict[str, str]) -> str:
    if env.get("GOOGLE_OAUTH_CLIENT_ID") and env.get("GOOGLE_OAUTH_CLIENT_SECRET"):
        return "oauth"
    return "anonymous"


def ensure_oauth_values(env: dict[str, str], updates: dict[str, str], interactive: bool) -> None:
    client_id = env.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = env.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    admin_email = env.get("PWIKI_ADMIN_GOOGLE_EMAIL", "")

    if interactive:
        if not client_id:
            client_id = prompt_text("Google OAuth client id")
        if not client_secret:
            client_secret = getpass.getpass("Google OAuth client secret: ").strip()
        if not admin_email:
            admin_email = prompt_text("Bootstrap admin Google email")

    updates["GOOGLE_OAUTH_CLIENT_ID"] = client_id
    updates["GOOGLE_OAUTH_CLIENT_SECRET"] = client_secret
    updates["PWIKI_ADMIN_GOOGLE_EMAIL"] = admin_email


def ensure_git_values(env: dict[str, str], updates: dict[str, str], interactive: bool) -> None:
    git_host_dir = env.get("PWIKI_GIT_HOST_DIR", "./obsidata")
    markdown_subdir = env.get("PWIKI_MARKDOWN_SUBDIR", "")

    if interactive:
        git_host_dir = prompt_text("Host Git working tree root", default=git_host_dir)
        markdown_subdir = prompt_text(
            "Markdown subdirectory inside that Git root (empty = root)",
            default=markdown_subdir,
            allow_empty=True,
        )

    updates["PWIKI_GIT_HOST_DIR"] = git_host_dir
    updates["PWIKI_MARKDOWN_SUBDIR"] = markdown_subdir


def prompt_choice(label: str, choices: list[tuple[str, str]], *, default: str) -> str:
    print(f"\n{label}:")
    for index, (value, description) in enumerate(choices, start=1):
        suffix = " (default)" if value == default else ""
        print(f"  {index}. {value}: {description}{suffix}")
    while True:
        answer = input(f"Choose [{default}]: ").strip()
        if not answer:
            return default
        for index, (value, _) in enumerate(choices, start=1):
            if answer == str(index) or answer.lower() == value:
                return value
        print("Invalid choice.")


def prompt_text(label: str, *, default: str = "", allow_empty: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"{label}{suffix}: ").strip()
        if answer:
            return answer
        if default:
            return default
        if allow_empty:
            return ""
        print("Value is required.")


def print_updates(updates: dict[str, str]) -> None:
    print("\nPlanned .env updates:")
    for key in sorted(updates):
        print(f"  {key}={mask_value(key, updates[key])}")


def validate_env(
    file_env: dict[str, str],
    env: dict[str, str],
    args: argparse.Namespace,
) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []

    if not file_env:
        problems.append(".env was not found or has no KEY=VALUE settings")

    secret = env.get("PWIKI_SECRET_KEY", "")
    secret_hint = (
        '  generate with: python3 -c "import secrets; print(secrets.token_urlsafe(48))"'
    )
    if not secret:
        problems.append("PWIKI_SECRET_KEY is required\n" + secret_hint)
    elif secret == "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET" or len(secret) < 32:
        problems.append("PWIKI_SECRET_KEY must be changed to a long random value\n" + secret_hint)

    use_git = truthy(env.get("PWIKI_USE_GIT", "1"))
    git_host_dir = Path(env.get("PWIKI_GIT_HOST_DIR", "./obsidata")).expanduser()
    if not git_host_dir.is_absolute():
        git_host_dir = (REPO_ROOT / git_host_dir).resolve()
    markdown_subdir = env.get("PWIKI_MARKDOWN_SUBDIR", "")
    markdown_host_dir = (git_host_dir / markdown_subdir).resolve()

    if not git_host_dir.is_dir():
        problems.append(f"PWIKI_GIT_HOST_DIR does not exist or is not a directory: {git_host_dir}")
    elif use_git and not (git_host_dir / ".git").exists():
        problems.append(f"PWIKI_GIT_HOST_DIR is not a Git working tree root: {git_host_dir}")

    if not markdown_host_dir.is_dir():
        problems.append(f"PWIKI_MARKDOWN_SUBDIR does not resolve to an existing directory: {markdown_host_dir}")
    else:
        try:
            markdown_host_dir.relative_to(git_host_dir)
        except ValueError:
            problems.append("PWIKI_MARKDOWN_SUBDIR resolves outside PWIKI_GIT_HOST_DIR")
        if not any(markdown_host_dir.rglob("*.md")):
            warnings.append(f"No Markdown files found under {markdown_host_dir}")

    oauth_id = env.get("GOOGLE_OAUTH_CLIENT_ID", "")
    oauth_secret = env.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    allow_anonymous = truthy(env.get("PWIKI_ALLOW_ANONYMOUS", "0"))
    if bool(oauth_id) != bool(oauth_secret):
        problems.append("GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set together")
    if not oauth_id and not allow_anonymous:
        problems.append("OAuth is not configured; set PWIKI_ALLOW_ANONYMOUS=1 for intentional anonymous read-only mode")

    read_only = truthy(env.get("PWIKI_READ_ONLY", "1"))
    auto_commit = truthy(env.get("PWIKI_GIT_AUTO_COMMIT", "0"))
    mount_mode = env.get("PWIKI_VAULT_MOUNT_MODE", "rw")
    if auto_commit and not use_git:
        problems.append("PWIKI_GIT_AUTO_COMMIT=1 requires PWIKI_USE_GIT=1")
    if auto_commit and read_only:
        problems.append("PWIKI_GIT_AUTO_COMMIT=1 requires PWIKI_READ_ONLY=0")
    if auto_commit and mount_mode == "ro":
        problems.append("PWIKI_GIT_AUTO_COMMIT=1 requires a writable vault mount")
    if not args.skip_docker and not shutil.which("docker"):
        problems.append("docker command was not found")
    if not args.skip_systemd and not shutil.which("systemctl"):
        warnings.append("systemctl command was not found; user timer install will fail unless --skip-systemd is used")

    if not (REPO_ROOT / "docker-compose.yml").is_file():
        problems.append("docker-compose.yml was not found in repo root")
    if not USER_SERVICE_TEMPLATE.is_file() or not USER_TIMER_TEMPLATE.is_file():
        problems.append("user systemd template files are missing under deploy/systemd")

    base_url = env.get("PWIKI_PUBLIC_BASE_URL", "")
    url_prefix = env.get("PWIKI_URL_PREFIX", "")
    if base_url:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            problems.append(
                f"PWIKI_PUBLIC_BASE_URL must use http:// or https://: {base_url}"
            )
        elif not parsed.netloc:
            problems.append(
                f"PWIKI_PUBLIC_BASE_URL is missing a host: {base_url}"
            )
        elif parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            problems.append(
                "PWIKI_PUBLIC_BASE_URL must be scheme+host only; put path in PWIKI_URL_PREFIX"
            )
    if url_prefix and not url_prefix.startswith("/"):
        problems.append("PWIKI_URL_PREFIX must start with /")
    if url_prefix.endswith("/"):
        problems.append("PWIKI_URL_PREFIX must not end with /")

    return problems, warnings


def print_summary(env_path: Path, env: dict[str, str], problems: list[str], warnings: list[str]) -> None:
    print(f"Repository: {REPO_ROOT}")
    print(f"Env file:   {env_path}")
    print("\nSettings:")
    keys = [
        "PWIKI_SECRET_KEY",
        "PWIKI_GIT_HOST_DIR",
        "PWIKI_MARKDOWN_SUBDIR",
        "PWIKI_USE_GIT",
        "PWIKI_READ_ONLY",
        "PWIKI_GIT_AUTO_COMMIT",
        "PWIKI_VAULT_MOUNT_MODE",
        "PWIKI_URL_PREFIX",
        "PWIKI_PUBLIC_BASE_URL",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "PWIKI_ADMIN_GOOGLE_EMAIL",
        "PWIKI_ALLOW_ANONYMOUS",
    ]
    for key in keys:
        if key in env:
            print(f"  {key}={mask_value(key, env[key])}")
        else:
            print(f"  {key}=(default)")

    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if problems:
        print("\nProblems:")
        for problem in problems:
            print(f"  - {problem}")


def print_proxy_and_oauth_guidance(env: dict[str, str]) -> None:
    base_url = env.get("PWIKI_PUBLIC_BASE_URL", "").rstrip("/")
    url_prefix = normalize_url_prefix(env.get("PWIKI_URL_PREFIX", ""))
    oauth_enabled = bool(env.get("GOOGLE_OAUTH_CLIENT_ID") and env.get("GOOGLE_OAUTH_CLIENT_SECRET"))

    if base_url or url_prefix:
        external_root = f"{base_url or 'https://YOUR_DOMAIN'}{url_prefix}"
        location = f"{url_prefix}/" if url_prefix else "/"
        print("\nNginx location snippet:")
        print("```nginx")
        print(f"location {location} {{")
        print("    proxy_pass         http://127.0.0.1:5000/;")
        print("    proxy_set_header   Host              $host;")
        print("    proxy_set_header   X-Real-IP         $remote_addr;")
        print("    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;")
        print("    proxy_set_header   X-Forwarded-Proto $scheme;")
        print("}")
        print("```")
        print(f"External URL: {external_root}/")
    else:
        external_root = "http://localhost:5000"

    if oauth_enabled:
        redirect_uri = f"{external_root}/auth/google/callback"
        print("\nGoogle OAuth setup:")
        print("  1. Google Cloud Console -> APIs & Services -> Credentials")
        print("  2. Create or edit an OAuth 2.0 Client ID with type Web application")
        print("  3. Add this Authorized redirect URI:")
        print(f"     {redirect_uri}")
        print("  4. Put the client id/secret in GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET")
        print("  5. Put the first admin email in PWIKI_ADMIN_GOOGLE_EMAIL")


def install_user_systemd(assume_yes: bool) -> None:
    USER_SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
    service_text = USER_SERVICE_TEMPLATE.read_text(encoding="utf-8").replace(
        "WorkingDirectory=/srv/newwiki",
        f"WorkingDirectory={REPO_ROOT}",
    )
    # The template's `/usr/bin/docker` is the Debian/Ubuntu default path.
    # snap, brew (/usr/local/bin), and other installs can use different paths,
    # so replace it with the docker binary found on this host.
    docker_path = shutil.which("docker")
    if docker_path and docker_path != "/usr/bin/docker":
        service_text = service_text.replace("/usr/bin/docker", docker_path)
    timer_text = USER_TIMER_TEMPLATE.read_text(encoding="utf-8")

    write_file_with_prompt(USER_SERVICE_TARGET, service_text, assume_yes)
    write_file_with_prompt(USER_TIMER_TARGET, timer_text, assume_yes)

    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", "pwiki-vault-sync.timer"])


def write_file_with_prompt(path: Path, content: str, assume_yes: bool) -> None:
    if path.exists() and path.read_text(encoding="utf-8") != content:
        if not assume_yes and not confirm(f"Overwrite {path}?"):
            raise SystemExit(1)
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path}")


def check_user_timer() -> None:
    run(["systemctl", "--user", "is-enabled", "pwiki-vault-sync.timer"], check=False)
    run(["systemctl", "--user", "is-active", "pwiki-vault-sync.timer"], check=False)
    run(["systemctl", "--user", "list-timers", "pwiki-vault-sync.timer"], check=False)


def maybe_enable_linger(assume_yes: bool) -> None:
    if not shutil.which("loginctl"):
        print("loginctl not found; skipping linger check")
        return
    user = os.environ.get("USER", "")
    if not user:
        print("USER is not set; skipping linger check")
        return
    status = subprocess.run(
        ["loginctl", "show-user", user, "-p", "Linger"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode == 0:
        print(status.stdout.strip())
        if "Linger=yes" in status.stdout:
            return
    if assume_yes or confirm("Enable linger so the --user timer can run without an active login session?"):
        run(["loginctl", "enable-linger", user], check=False)
        run(["loginctl", "show-user", user, "-p", "Linger"], check=False)


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print(f"\n$ {shlex.join(command)}")
    result = subprocess.run(command, cwd=cwd, env=env, check=False, text=True)
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def normalize_url_prefix(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    return value if value.startswith("/") else f"/{value}"


def mask_value(key: str, value: str) -> str:
    if key not in SECRET_KEYS or not value:
        return value
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}...{value[-4:]}"


if __name__ == "__main__":
    raise SystemExit(main())
