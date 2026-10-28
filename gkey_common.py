"""Shared helpers for gkey: data dir, local placeholder key, secret masking, pool sizing."""

from __future__ import annotations

import os
import pathlib
import re
import shlex
from typing import Optional, Sequence

# The local proxy ignores API keys entirely. This is a fixed, public, NON-SECRET
# placeholder that is handed to wrapped tools so that they stop asking for a key.
# gkey never forwards a real OPENAI_API_KEY from your environment.
LOCAL_API_KEY = "gkey-local-placeholder"

DEFAULT_POOL_SIZE = 4
MAX_POOL_SIZE = 16


def gkey_home() -> pathlib.Path:
    """Per-user data directory (venv, pid files, logs, workspaces, tool clones)."""
    return pathlib.Path(os.environ.get("GKEY_HOME") or (pathlib.Path.home() / ".gkey"))


def package_dir() -> pathlib.Path:
    """Directory that contains this package (never hardcoded)."""
    return pathlib.Path(__file__).resolve().parent


def mask_secret(value: Optional[str]) -> str:
    """Return a safe-to-print version of a secret (never the full value)."""
    if not value:
        return "(empty)"
    if value == LOCAL_API_KEY:
        return value  # public placeholder, nothing to hide
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}...{value[-2:]} ({len(value)} chars)"


_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,})")


def redact_text(text: str) -> str:
    """Mask anything that looks like an API key inside free text."""
    return _SECRET_RE.sub(lambda m: mask_secret(m.group(0)), text)


# Per-tool flag that controls the tool's own concurrency. Best effort: when one of
# these is present we size the proxy pool to match so the tool is not starved.
CONCURRENCY_FLAGS = {
    "pdf2zh": ("-t", "--thread"),
    "babeldoc": ("--qps", "--pool-max-workers"),
    "gpt-subtrans": ("--maxthreads",),
}


def detect_concurrency(tool_id: str, args: Sequence[str]) -> Optional[int]:
    """Find the tool's thread/concurrency setting in args (e.g. pdf2zh -t 8)."""
    flags = CONCURRENCY_FLAGS.get(tool_id)
    if not flags:
        return None
    found: Optional[int] = None
    args = list(args)
    for i, a in enumerate(args):
        for f in flags:
            val: Optional[str] = None
            if a == f and i + 1 < len(args):
                val = args[i + 1]
            elif a.startswith(f + "="):
                val = a.split("=", 1)[1]
            elif not f.startswith("--") and a.startswith(f) and len(a) > len(f) and a[len(f):].isdigit():
                val = a[len(f):]  # -t8
            if val is not None and val.isdigit() and int(val) > 0:
                found = max(found or 0, int(val))
    return found


def compute_pool_size(
    default: int,
    explicit: Optional[int] = None,
    detected: Optional[int] = None,
    cap: int = MAX_POOL_SIZE,
) -> int:
    """Pool size: explicit --pool-size wins; else max(default, detected); always 1..cap."""
    if explicit is not None:
        size = explicit
    else:
        size = max(default, detected or 0)
    return max(1, min(size, cap))


def format_command(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)
