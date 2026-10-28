"""Opt-in speed options shared by every tool: --fast, --batch, --parallel-files.

Catalog contract (all keys optional; tools without them keep working unchanged):

  ToolSpec(..., fast={"flags": ["-t", "16"], "env": {"X": "1"}, "pool": 16,
                      "aliases": {"-t": ["--thread"]}},
               batch_flag="--maxbatchsize",
               per_file={"value_flags": ["-o", "--output", ...]})

or, without touching the ToolSpec entry, the top-level ``catalog.SPEED[tool_id]`` dict with the
same keys (``fast``, ``batch_flag``, ``per_file``). The ToolSpec attribute wins when both exist.

Rules: flags/env are only added when the user did not pass/set them (user always wins); ``pool``
is the minimum proxy worker pool; ``per_file`` marks a tool that takes one input file per run, so
``--parallel-files N`` may split several inputs into N concurrent runs. ``value_flags`` lists
options that take a separate value (so the value is not mistaken for an input file).

The launcher passes the user's choices in environment variables: GKEY_FAST=1, GKEY_BATCH=<n>,
GKEY_PARALLEL_FILES=<n>.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence

from gkey_common import CONCURRENCY_FLAGS, MAX_POOL_SIZE, detect_concurrency


def _int_env(name: str) -> Optional[int]:
    v = os.environ.get(name, "")
    return int(v) if v.isdigit() and int(v) > 0 else None


def speed_options() -> Dict[str, Any]:
    return {
        "fast": os.environ.get("GKEY_FAST", "") not in ("", "0"),
        "batch": _int_env("GKEY_BATCH"),
        "parallel": _int_env("GKEY_PARALLEL_FILES"),
    }


def tool_config(spec: Any, key: str, table: Optional[dict] = None) -> Any:
    """Per-tool setting: ToolSpec attribute first, then the catalog.SPEED[tool_id] table."""
    val = getattr(spec, key, None)
    if val:
        return val
    return (table or {}).get(getattr(spec, "id", ""), {}).get(key) or None


def has_flag(args: Sequence[str], names: Sequence[str]) -> bool:
    """True if any of `names` appears in args as `-x v`, `--x=v` or attached short `-x8`."""
    for a in args:
        if a == "--":
            break
        for n in names:
            if a == n or a.startswith(n + "="):
                return True
            if not n.startswith("--") and a.startswith(n) and len(a) > len(n) and a[len(n):].isdigit():
                return True
    return False


def _groups(tokens: Sequence[str]) -> List[List[str]]:
    groups: List[List[str]] = []
    for t in tokens:
        if t.startswith("-") and len(t) > 1 and not t[1:].isdigit():
            groups.append([t])
        elif groups:
            groups[-1].append(t)
        else:
            groups.append([t])
    return groups


def fast_flag_names(tool_id: str, profile: dict, name: str) -> List[str]:
    names = [name] + list((profile.get("aliases") or {}).get(name, []))
    known = CONCURRENCY_FLAGS.get(tool_id, ())
    if name in known:  # e.g. pdf2zh: -t and --thread are the same option
        names.extend(known)
    return names


def apply_fast(tool_id: str, args: List[str], profile: Optional[dict], env: dict,
               environ: Optional[dict] = None) -> "tuple[List[str], List[str]]":
    """Inject the profile's flags/env unless the user set them. Returns (args, human notes)."""
    environ = os.environ if environ is None else environ
    notes: List[str] = []
    if not profile:
        return list(args), ["no tuned profile for this tool yet; running with its defaults"]
    out = list(args)
    added: List[str] = []
    for grp in _groups(profile.get("flags") or []):
        if grp[0].startswith("-") and not has_flag(out, fast_flag_names(tool_id, profile, grp[0])):
            out.extend(grp)
            added.extend(grp)
    for k, v in (profile.get("env") or {}).items():
        if k not in environ and k not in env:
            env[k] = str(v)
            notes.append(f"env {k}={v}")
    if added:
        notes.insert(0, "added " + " ".join(added))
    if not notes:
        notes.append("your own flags already cover the profile")
    return out, notes


def apply_batch(args: List[str], batch: Optional[int], batch_flag: Optional[str]) -> "tuple[List[str], Optional[str]]":
    if not batch:
        return list(args), None
    if not batch_flag:
        return list(args), "this tool has no known batch-size option; --batch ignored"
    if has_flag(args, [batch_flag]):
        return list(args), f"{batch_flag} already given; keeping yours"
    return list(args) + [batch_flag, str(batch)], f"added {batch_flag} {batch}"


def speed_pool_hint(spec: Any, args: Sequence[str], table: Optional[dict] = None) -> Optional[int]:
    """Pool size the speed options want (None = no opinion). Honoured by `catalog.py poolsize`."""
    opts = speed_options()
    if spec is None or not (opts["fast"] or opts["parallel"]):
        return None
    want = 0
    per_run = 1
    if opts["fast"]:
        prof = tool_config(spec, "fast", table)
        if prof:
            want = int(prof.get("pool") or 0)
            per_run = max(per_run, detect_concurrency(spec.id, list(args) + list(prof.get("flags") or [])) or 1)
    if opts["parallel"]:
        per_run = max(per_run, detect_concurrency(spec.id, args) or 1)
        want = max(want, opts["parallel"] * per_run)
    return min(want, MAX_POOL_SIZE) or None


# ---- --parallel-files ----------------------------------------------------------------------

def find_inputs(args: Sequence[str], value_flags: Optional[Sequence[str]] = None) -> List[int]:
    """Indices of positional args that are existing files. Without `value_flags`, any token
    right after a dash-option is assumed to be that option's value (conservative)."""
    vf = set(value_flags) if value_flags is not None else None
    idx: List[int] = []
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a.startswith("-") and len(a) > 1:
            if "=" not in a and (vf is None or a in vf):
                skip = True
            continue
        if os.path.isfile(a.strip("\"'")):
            idx.append(i)
    return idx


def build_runs(args: Sequence[str], idx: Sequence[int]) -> List[List[str]]:
    """One argv tail per input file: other args unchanged, that file in the first input's slot."""
    drop = set(idx)
    runs = []
    for keep in idx:
        run: List[str] = []
        for i, a in enumerate(args):
            if i == idx[0]:
                run.append(args[keep])
            elif i not in drop:
                run.append(a)
        runs.append(run)
    return runs


def run_parallel(
    binary: str,
    runs: Sequence[List[str]],
    labels: Sequence[str],
    workers: int,
    post: Optional[Callable[[List[str]], None]] = None,
    out=None,
) -> int:
    """Run argv tails concurrently (<= workers at once), each line prefixed with `[label]`.
    Returns 0 if every run succeeded, else the first non-zero exit code (in input order)."""
    out = out or sys.stderr
    lock = threading.Lock()
    codes: List[int] = [0] * len(runs)
    sem = threading.Semaphore(max(1, workers))
    procs: Dict[int, subprocess.Popen] = {}

    def emit(label: str, text: str) -> None:
        with lock:
            out.write(f"[{label}] {text}\n")
            out.flush()

    def work(i: int) -> None:
        with sem:
            label = labels[i]
            try:
                p = subprocess.Popen([binary] + list(runs[i]), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
            except FileNotFoundError:
                emit(label, f"command not found: {binary}")
                codes[i] = 127
                return
            procs[i] = p
            assert p.stdout is not None
            for line in p.stdout:
                # tqdm-style progress uses \r; keep only the latest frame of each line
                emit(label, line.rstrip("\n").split("\r")[-1])
            codes[i] = p.wait()
            if codes[i] == 0 and post is not None:
                try:
                    post(list(runs[i]))
                except Exception as e:  # noqa: BLE001
                    emit(label, f"post-step failed: {e}")
            emit(label, f"exit {codes[i]}")

    threads = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(len(runs))]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        for p in list(procs.values()):
            if p.poll() is None:
                p.terminate()
        return 130
    failed = [labels[i] for i, c in enumerate(codes) if c != 0]
    with lock:
        out.write(f"[gkey] parallel-files: {len(runs) - len(failed)}/{len(runs)} succeeded"
                  + (f"; failed: {', '.join(failed)}" if failed else "") + "\n")
    return next((c for c in codes if c != 0), 0)


def unique_labels(args: Sequence[str], idx: Sequence[int]) -> List[str]:
    seen: Dict[str, int] = {}
    labels = []
    for i in idx:
        base = os.path.basename(args[i]) or args[i]
        seen[base] = seen.get(base, 0) + 1
        labels.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return labels
