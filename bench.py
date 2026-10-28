#!/usr/bin/env python3
"""gkey benchmark: measure local proxy overhead without calling any real model.

  bench.py --bench [--concurrency N] [--requests M]   fake backend, real HTTP path
  bench.py --real                                     ONE tiny prompt per backend (explicit opt-in)
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

HERE = pathlib.Path(__file__).resolve().parent
FAKE_DELAY_MS = 20.0

NOTE = """\
How to read this:
  * The fake backend sleeps {delay:g} ms per request and returns canned text, so these numbers are
    the PROXY's own overhead (HTTP, JSON, SSE framing, pool queueing), not model latency.
  * With concurrency > pool size, requests queue: expect latency ~ ceil(conc/pool) x {delay:g} ms.
    Raise it with --pool-size N (max 16).
  * --cache only helps when prompts repeat; use --dup-pct to say how often (real workloads may differ).
  * Real latency = proxy overhead + model time (usually seconds), so overhead of a few ms is noise.
  * To compare backends on real calls, do it yourself with a tiny prompt, e.g.
      gkey -b agy mods "reply with OK"    vs    gkey -b sdk mods "reply with OK"
    or run `gkey --bench-real` (sends exactly one tiny prompt per backend)."""


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; values need not be sorted."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(1, math.ceil(pct / 100.0 * len(s)))
    return s[k - 1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_json(port: int, method: str, path: str, body: Optional[dict] = None, timeout: float = 30) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def chat_once(port: int, stream: bool, prompt: str = "bench", model: str = "bench", timeout: float = 120) -> tuple[float, float, str]:
    """One chat request. Returns (time_to_first_content_s, total_s, text); raises on HTTP error."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    t0 = time.perf_counter()
    try:
        body = {"model": model, "stream": stream, "messages": [{"role": "user", "content": prompt}]}
        conn.request("POST", "/v1/chat/completions", body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}: {r.read()[:200]!r}")
        if not stream:
            payload = json.loads(r.read())
            t = time.perf_counter() - t0
            return t, t, payload["choices"][0]["message"]["content"]
        ttft = 0.0
        parts: list[str] = []
        while True:
            line = r.readline()
            if not line:
                break
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            obj = json.loads(data)
            if "error" in obj:
                raise RuntimeError(str(obj["error"]))
            delta = obj["choices"][0]["delta"].get("content")
            if delta:
                if not parts:
                    ttft = time.perf_counter() - t0
                parts.append(delta)
        return ttft, time.perf_counter() - t0, "".join(parts)
    finally:
        conn.close()


def prompt_for(i: int, dup_pct: int) -> tuple[str, bool]:
    """(prompt, is_duplicate). dup_pct % of requests cycle through 5 repeated prompts."""
    if dup_pct > 0 and (i % 100) < dup_pct:
        return f"bench-dup-{i % 5}", True
    return f"bench-{i}", False


def run_load(port: int, concurrency: int, requests: int, stream_ratio_streaming: bool = True, dup_pct: int = 0) -> dict:
    """Fire `requests` requests with `concurrency` client threads; alternate non-stream / stream.
    Duplicate prompts (--dup-pct) are always non-streaming, because only those can be cached."""
    lock = threading.Lock()
    counter = [0]
    lat: list[float] = []
    errors: list[str] = []

    def worker() -> None:
        while True:
            with lock:
                i = counter[0]
                if i >= requests:
                    return
                counter[0] += 1
            try:
                prompt, dup = prompt_for(i, dup_pct)
                _, total, _ = chat_once(port, stream=bool(i % 2) and stream_ratio_streaming and not dup, prompt=prompt)
                with lock:
                    lat.append(total)
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(str(e))

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(min(concurrency, requests))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0
    return {
        "ok": len(lat),
        "errors": errors,
        "wall_s": wall,
        "rps": len(lat) / wall if wall > 0 else 0.0,
        "p50_ms": percentile(lat, 50) * 1000,
        "p95_ms": percentile(lat, 95) * 1000,
        "max_ms": max(lat) * 1000 if lat else 0.0,
    }


def start_server(extra: list[str], pool_size: int, model: str, effort: str, env_extra: Optional[dict] = None) -> tuple[subprocess.Popen, int]:
    port = free_port()
    env = {**os.environ, **(env_extra or {})}
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), "--host", "127.0.0.1", "--port", str(port),
         "--pool-size", str(pool_size), "--model", model, "--effort", effort, *extra],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("proxy exited during startup")
        try:
            status, _ = http_json(port, "GET", "/health", timeout=1)
            if status == 200:
                return proc, port
        except OSError:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("timed out waiting for the proxy")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


CONFIG_KEYS = {"pool": "pool_size", "cache": "cache", "dup": "dup_pct", "conc": "concurrency",
               "reqs": "requests", "delay": "delay_ms"}


def parse_config(spec: str, base: dict) -> dict:
    """`pool=16,cache=on,dup=50` -> config dict layered over `base` (unknown keys are an error)."""
    cfg = dict(base)
    cfg["label"] = spec
    for part in filter(None, (p.strip() for p in spec.split(","))):
        k, _, v = part.partition("=")
        if k not in CONFIG_KEYS or not v:
            raise ValueError(f"bad --compare item '{part}' (keys: {', '.join(CONFIG_KEYS)})")
        if k == "cache":
            if v.lower() not in ("on", "off", "1", "0", "true", "false"):
                raise ValueError("cache must be on or off")
            cfg["cache"] = v.lower() in ("on", "1", "true")
        else:
            cfg[CONFIG_KEYS[k]] = float(v) if k == "delay" else int(v)
    cfg["pool_size"] = max(1, min(cfg["pool_size"], 16))
    cfg["concurrency"] = max(1, cfg["concurrency"])
    cfg["requests"] = max(1, cfg["requests"])
    cfg["dup_pct"] = max(0, min(cfg["dup_pct"], 100))
    return cfg


def run_config(cfg: dict, model: str, effort: str) -> dict:
    """Start a throwaway fake-backend proxy for one config, load it, return results + health."""
    extra = ["--fake-backend", "--fake-delay-ms", str(cfg["delay_ms"])] + (["--cache"] if cfg["cache"] else [])
    proc, port = start_server(extra, cfg["pool_size"], model, effort)
    try:
        health = json.loads(http_json(port, "GET", "/health")[1])
        chat_once(port, False, prompt="warmup")  # warm-up (not counted)
        res = run_load(port, cfg["concurrency"], cfg["requests"], dup_pct=cfg["dup_pct"])
        health = json.loads(http_json(port, "GET", "/health")[1])  # re-read for cache stats
    finally:
        stop_server(proc)
    res["health"] = health
    return res


def fmt_rate(health: dict) -> str:
    if not health.get("cache_enabled"):
        return "off"
    r = health.get("cache_hit_rate")
    return "n/a" if r is None else f"{r * 100:.0f}%"


def cmd_bench(args: argparse.Namespace) -> int:
    base = {"pool_size": args.pool_size, "cache": args.cache, "dup_pct": args.dup_pct,
            "concurrency": args.concurrency, "requests": args.requests, "delay_ms": args.delay_ms, "label": "baseline"}
    try:
        cfgs = [parse_config(c, base) for c in args.compare] if args.compare else [parse_config("", base)]
    except ValueError as e:
        print(f"gkey bench: {e}", file=sys.stderr)
        return 2
    results = [run_config(c, args.model, args.effort) for c in cfgs]
    bad = any(r["errors"] or r["ok"] == 0 for r in results)
    delay = cfgs[0]["delay_ms"]

    if not args.compare:
        c, res = cfgs[0], results[0]
        health = res["health"]
        print(f"gkey bench (FAKE backend, no model calls; simulated model delay {delay:g} ms)")
        print(f"  requests      : {res['ok']}/{c['requests']} ok (half streaming, half non-streaming)")
        print(f"  concurrency   : {c['concurrency']}")
        print(f"  pool size     : {health.get('pool_size')} (effective parallelism = min(concurrency, pool) = "
              f"{min(c['concurrency'], health.get('pool_size') or 1)})")
        print(f"  latency p50   : {res['p50_ms']:.1f} ms")
        print(f"  latency p95   : {res['p95_ms']:.1f} ms")
        print(f"  latency max   : {res['max_ms']:.1f} ms")
        print(f"  throughput    : {res['rps']:.1f} req/s")
        if c["cache"] or c["dup_pct"]:
            print(f"  cache         : {fmt_rate(health)} hit rate ({c['dup_pct']}% duplicate prompts in the load)")
        if res["errors"]:
            print(f"  errors        : {len(res['errors'])} (first: {res['errors'][0]})")
    else:
        print(f"gkey bench --compare (FAKE backend, no model calls; base delay {delay:g} ms)")
        head = f"  {'config':<28} {'ok':>7} {'p50 ms':>8} {'p95 ms':>8} {'req/s':>8} {'cache':>6}  vs first"
        print(head)
        base_rps = results[0]["rps"] or 1e-9
        for c, r in zip(cfgs, results):
            ok = f"{r['ok']}/{c['requests']}"
            print(f"  {c['label']:<28} {ok:>7} {r['p50_ms']:>8.1f} {r['p95_ms']:>8.1f} {r['rps']:>8.1f} "
                  f"{fmt_rate(r['health']):>6}  {r['rps'] / base_rps:>5.2f}x")
        for c, r in zip(cfgs, results):
            if r["errors"]:
                print(f"  errors in '{c['label']}': {len(r['errors'])} (first: {r['errors'][0]})")
    print()
    print(NOTE.format(delay=delay))
    return 1 if bad else 0


def cmd_real(args: argparse.Namespace) -> int:
    print("gkey bench-real: sending ONE tiny prompt per backend (this uses a real model call each).")
    rc = 0
    for backend in ("agy", "sdk"):
        try:
            proc, port = start_server(["--backend", backend], args.pool_size, args.model, args.effort)
        except Exception as e:  # noqa: BLE001
            print(f"  {backend}: could not start proxy ({e})")
            rc = 1
            continue
        try:
            health = json.loads(http_json(port, "GET", "/health")[1])
            if backend == "sdk" and not health.get("sdk_available"):
                print("  sdk: skipped (google-antigravity package not installed)")
                continue
            deadline = time.time() + 30
            while backend == "agy" and time.time() < deadline:  # wait for a warm worker, not counted
                h = json.loads(http_json(port, "GET", "/health")[1])
                if h.get("warm_workers") or h.get("last_warm_error"):
                    break
                time.sleep(0.2)
            ttft, total, text = chat_once(port, True, prompt="reply with OK", model=args.model, timeout=120)
            print(f"  {backend}: time-to-first-token {ttft * 1000:.0f} ms, total {total * 1000:.0f} ms, reply={text.strip()[:40]!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  {backend}: failed ({e})")
            rc = 1
        finally:
            stop_server(proc)
    print("Note: includes model latency; warm workers were awaited first, so cold start is excluded.")
    return rc


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="gkey benchmark")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--bench", action="store_true", help="fake-backend load test (default)")
    mode.add_argument("--real", action="store_true", help="one tiny real prompt per backend")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--requests", type=int, default=40)
    p.add_argument("--pool-size", type=int, default=4)
    p.add_argument("--model", default="gemini-3.8-flash-low")
    p.add_argument("--effort", default="low")
    p.add_argument("--cache", action="store_true", help="enable the proxy response cache for the baseline config")
    p.add_argument("--dup-pct", type=int, default=0, help="percent of requests repeating an earlier prompt")
    p.add_argument("--delay-ms", type=float, default=FAKE_DELAY_MS, help="simulated model time per request")
    p.add_argument("--compare", action="append", default=[], help="config spec, repeatable: pool=16,cache=on,dup=50")
    args = p.parse_args(argv)
    args.concurrency = max(1, args.concurrency)
    args.requests = max(1, args.requests)
    args.pool_size = max(1, min(args.pool_size, 16))
    return cmd_real(args) if args.real else cmd_bench(args)


if __name__ == "__main__":
    sys.exit(main())
