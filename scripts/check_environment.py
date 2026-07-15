"""Check local configuration and dependent service availability."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

REQUIRED_ENV = (
    "GLM_API_KEY",
    "GLM_BASE_URL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "QWEN_API_KEY",
    "QWEN_BASE_URL",
    "OPENSANDBOX_API_KEY",
)

SERVICES = {
    "MongoDB": os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    "Java ERP API": os.getenv("JAVA_API_BASE_URL", "http://localhost:8080/api"),
    "OpenSandbox": os.getenv("SANDBOX_DOMAIN", "http://localhost:8081"),
}


def endpoint(url: str) -> tuple[str, int]:
    """Extract a TCP endpoint from an HTTP or MongoDB URL."""
    parsed = urlparse(url)
    defaults = {"http": 80, "https": 443, "mongodb": 27017}
    return parsed.hostname or "localhost", parsed.port or defaults[parsed.scheme]


def is_reachable(host: str, port: int) -> bool:
    """Return whether a TCP endpoint accepts a connection."""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def main() -> int:
    """Print an environment report and return a shell-friendly status code."""
    failed = False
    print(f"Python: {sys.version.split()[0]}")

    for name in REQUIRED_ENV:
        configured = bool(os.getenv(name))
        print(f"[{'OK' if configured else '缺失'}] 环境变量 {name}")
        failed |= not configured

    for name, url in SERVICES.items():
        host, port = endpoint(url)
        reachable = is_reachable(host, port)
        print(f"[{'OK' if reachable else '不可达'}] {name}: {host}:{port}")
        failed |= not reachable

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
