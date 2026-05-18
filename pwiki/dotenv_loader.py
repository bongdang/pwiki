"""Tiny .env loader for local dev.

Reads simple KEY=VALUE pairs from a `.env` file in the current working
directory and copies them into `os.environ`. Already-set environment
variables win (so shell exports override the file). Production deployments
should not rely on this — they supply env via docker-compose / systemd.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_cwd_dotenv() -> None:
    dotenv_path = Path.cwd() / ".env"
    if not dotenv_path.is_file():
        return

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if not key or key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value
