#!/usr/bin/env python3
"""OpenAI-compatible API Proxy Server powered by Google Antigravity.

Supports two backends:
1. `sdk` (google-antigravity Python SDK):
   Uses `Agent(LocalAgentConfig(capabilities=CapabilitiesConfig(enabled_tools=[]), ...))`
   when `GEMINI_API_KEY` or Vertex AI (`GOOGLE_CLOUD_PROJECT`) is configured,
   or when `--backend sdk` is explicitly selected.
2. `agy` (Antigravity CLI warm stream-json worker pool):
   Uses your logged-in `agy` OAuth session with a zero-tool custom agent (`gkey-raw`)
   and pre-warmed persistent `--input-format stream-json` workers for zero-API-key usage.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import pathlib
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gkey_common import MAX_POOL_SIZE, gkey_home  # noqa: E402

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
import uvicorn

# The Google Antigravity Python SDK is optional: without it only the `agy` backend works.
try:
    from google.antigravity import (
        Agent,
        CapabilitiesConfig,
        CustomSystemInstructions,
        LocalAgentConfig,
    )

    SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    SDK_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("gkey-proxy")

# Overall deadline for one agy request, and for starting one worker (seconds).
REQUEST_TIMEOUT = float(os.environ.get("GKEY_TIMEOUT", "300"))
START_TIMEOUT = float(os.environ.get("GKEY_START_TIMEOUT", "60"))


def workspace_root() -> pathlib.Path:
    return gkey_home() / "workspace"

AGY_MODELS = {
    "gemini-4-argon-medium",
    "gemini-4-argon-high",
    "gemini-4-argon-low",
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "claude-opus-5-5-low",
    "claude-opus-5-5-medium",
    "claude-opus-5-5-high",
    "claude-sonnet-5-5-low",
    "claude-sonnet-5-5-medium",
    "claude-sonnet-5-5-high",
    "gpt-oss-120b-medium",
}

DEFAULT_SYSTEM_PROMPT = (
    "You are a stateless LLM API backend. "
    "Follow the system and user instructions strictly. "
    "Output ONLY the direct response requested by the user without conversational filler, "
    "markdown wrappers (unless requested), or explanations."
)



# ---- opt-in speed features: retries, response cache, coalescing ---------------------------------

# Substrings (lower case) of error messages that look like transient backend trouble.
TRANSIENT_MARKERS = (
    "closed its output", "exited before init", "did not initialise", "connection reset",
    "connection refused", "broken pipe", "temporarily", "temporary", "overloaded", "unavailable",
    "rate limit", "rate_limit", "too many requests", "429", "500", "502", "503", "504",
    "resource exhausted", "resource_exhausted", "internal error", "try again", "reset by peer",
)
PERMANENT_MARKERS = ("invalid", "unauthorized", "unauthenticated", "permission", "not installed", "not found", "api key")

RETRY_BASE_DELAY = 0.5
RETRY_MAX_DELAY = 8.0


async def _sleep(seconds: float) -> None:  # separate function so tests can patch it
    await asyncio.sleep(seconds)


def is_transient_error(exc: BaseException) -> bool:
    """Heuristic: is this backend failure worth retrying? Deadline timeouts are NOT retried."""
    if isinstance(exc, (asyncio.CancelledError, TimeoutError, asyncio.TimeoutError)):
        return False
    if isinstance(exc, (ConnectionError, BrokenPipeError)):
        return True
    msg = str(exc).lower()
    if any(m in msg for m in PERMANENT_MARKERS):
        return False
    return any(m in msg for m in TRANSIENT_MARKERS)


def backoff_delay(attempt: int, base: float = RETRY_BASE_DELAY, cap: float = RETRY_MAX_DELAY) -> float:
    """Exponential backoff with jitter (50-100% of base * 2**attempt, capped)."""
    return min(cap, base * (2 ** attempt)) * random.uniform(0.5, 1.0)


async def retrying_stream(
    backend: Any,
    retries: int,
    on_retry: Any = None,
    **kwargs: Any,
) -> AsyncIterator[str]:
    """backend.stream_chat with retries. Only retries while nothing has been yielded yet."""
    attempt = 0
    while True:
        yielded = False
        try:
            async for delta in backend.stream_chat(**kwargs):
                yielded = True
                yield delta
            return
        except Exception as e:  # noqa: BLE001
            if yielded or attempt >= retries or not is_transient_error(e):
                raise
            delay = backoff_delay(attempt)
            attempt += 1
            logger.warning("Transient backend error (%s); retry %d/%d in %.2fs", e, attempt, retries, delay)
            if on_retry is not None:
                on_retry()
            await _sleep(delay)


def cache_key(body: dict[str, Any], context: str) -> str:
    """Stable key over model, messages and sampling params (stream/user flags excluded)."""
    skip = {"stream", "stream_options", "user"}
    payload = {k: v for k, v in body.items() if k not in skip}
    payload["__ctx"] = context  # engine/default model/effort: never serve answers across them
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ResponseCache:
    """Size-bounded LRU of response texts, optionally mirrored to disk. Errors are never stored."""

    def __init__(
        self,
        max_entries: int = 512,
        max_bytes: int = 32 * 1024 * 1024,
        disk_dir: Optional[pathlib.Path] = None,
        max_disk_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self.max_entries = max(1, max_entries)
        self.max_bytes = max_bytes
        self.disk_dir = disk_dir
        self.max_disk_bytes = max_disk_bytes
        self._mem: "OrderedDict[str, str]" = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.coalesced = 0
        if disk_dir is not None:
            disk_dir.mkdir(parents=True, exist_ok=True)

    def _disk_path(self, key: str) -> pathlib.Path:
        assert self.disk_dir is not None
        return self.disk_dir / f"{key}.txt"

    def get(self, key: str) -> Optional[str]:
        text = self._mem.get(key)
        if text is not None:
            self._mem.move_to_end(key)
            return text
        if self.disk_dir is not None:
            try:
                text = self._disk_path(key).read_text(encoding="utf-8")
            except OSError:
                return None
            self._remember(key, text)
            return text
        return None

    def _remember(self, key: str, text: str) -> None:
        size = len(text.encode("utf-8"))
        if size > self.max_bytes // 2:
            return
        old = self._mem.pop(key, None)
        if old is not None:
            self._bytes -= len(old.encode("utf-8"))
        self._mem[key] = text
        self._bytes += size
        while self._mem and (len(self._mem) > self.max_entries or self._bytes > self.max_bytes):
            _, ev = self._mem.popitem(last=False)
            self._bytes -= len(ev.encode("utf-8"))

    def put(self, key: str, text: str) -> None:
        if not text or not text.strip():
            return
        self._remember(key, text)
        if self.disk_dir is not None and len(text.encode("utf-8")) <= self.max_disk_bytes // 4:
            try:
                tmp = self.disk_dir / f"{key}.{os.getpid()}.tmp"
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, self._disk_path(key))
                self._trim_disk()
            except OSError as e:
                logger.warning("cache: disk write failed: %s", e)

    def _trim_disk(self) -> None:
        assert self.disk_dir is not None
        files = []
        total = 0
        for p in self.disk_dir.glob("*.txt"):
            try:
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= self.max_disk_bytes:
            return
        for _, size, p in sorted(files):
            try:
                p.unlink()
            except OSError:
                continue
            total -= size
            if total <= self.max_disk_bytes * 0.9:
                break

    def hit_rate(self) -> Optional[float]:
        served = self.hits + self.coalesced
        total = served + self.misses
        return None if total == 0 else served / total

    def stats(self) -> dict[str, Any]:
        rate = self.hit_rate()
        return {
            "cache_enabled": True,
            "cache_hits": self.hits,
            "cache_misses": self.misses,
            "cache_coalesced": self.coalesced,
            "cache_hit_rate": None if rate is None else round(rate, 3),
            "cache_entries": len(self._mem),
            "cache_bytes": self._bytes,
            "cache_disk": self.disk_dir is not None,
        }


def format_openai_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Extracts system instructions and formats conversation messages into a prompt."""
    system_parts: list[str] = []
    conv_parts: list[str] = []
    img_dir = workspace_root() / "agy"
    img_dir.mkdir(parents=True, exist_ok=True)

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_chunks = []
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "text":
                        text_chunks.append(item.get("text", ""))
                    elif item_type == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url.startswith("data:image/") and ";base64," in url:
                            header, b64_data = url.split(";base64,", 1)
                            ext = header.split("/")[-1].split(";")[0] or "png"
                            img_path = img_dir / f"vision_{uuid.uuid4().hex[:12]}.{ext}"
                            try:
                                img_path.write_bytes(base64.b64decode(b64_data))
                                text_chunks.append(
                                    f"[Attached Image: @{img_path.name} (located at {img_path})]"
                                )
                            except Exception:
                                pass
                        elif url:
                            text_chunks.append(f"[Image URL: {url}]")
                elif isinstance(item, str):
                    text_chunks.append(item)
            content = "\n".join(text_chunks)
        else:
            content = str(content)

        if role in ("system", "developer"):
            if content.strip():
                system_parts.append(content.strip())
        elif role == "assistant":
            conv_parts.append(f"<assistant>\n{content}\n</assistant>")
        else:
            conv_parts.append(content)

    system_text = "\n\n".join(system_parts) if system_parts else ""
    if len(conv_parts) == 1:
        user_prompt = conv_parts[0]
    else:
        user_prompt = "\n\n".join(conv_parts)

    return system_text, user_prompt


def build_combined_prompt(
    messages: list[dict[str, Any]],
    response_format: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Returns (system_instructions, user_prompt, combined_single_turn_prompt)."""
    system_text, user_prompt = format_openai_messages(messages)
    effective_system = (
        f"{DEFAULT_SYSTEM_PROMPT}\n\n{system_text}"
        if system_text
        else DEFAULT_SYSTEM_PROMPT
    )

    schema_instruction = ""
    if response_format:
        rf_type = response_format.get("type")
        if rf_type == "json_object":
            schema_instruction = (
                "\n\nIMPORTANT: You MUST respond with valid, raw JSON only. "
                "Do not wrap the JSON in ```json markdown blocks."
            )
        elif rf_type == "json_schema":
            schema = response_format.get("json_schema", {}).get("schema", {})
            schema_instruction = (
                "\n\nIMPORTANT: You MUST respond with valid, raw JSON matching this schema:\n"
                f"{json.dumps(schema, ensure_ascii=False)}\n"
                "Do not wrap the JSON in ```json markdown blocks."
            )

    effective_system += schema_instruction

    if system_text or schema_instruction:
        combined = (
            "<system_instructions>\n"
            f"{system_text}{schema_instruction}\n"
            "Output ONLY the direct response without any preamble or explanation.\n"
            "</system_instructions>\n\n"
            f"{user_prompt}"
        )
    else:
        combined = user_prompt

    return effective_system, user_prompt, combined


class SDKBackend:
    """Backend using the official `google-antigravity` Python SDK (`LocalAgentConfig`)."""

    def __init__(self, default_model: str, concurrency: int = 4):
        self.default_model = default_model
        self._concurrency = concurrency
        self._semaphore: Optional[asyncio.Semaphore] = None
        self.workspace_dir = workspace_root() / "sdk"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    @property
    def semaphore(self) -> asyncio.Semaphore:
        # Created lazily: on Python < 3.10 asyncio primitives bind to the loop at construction.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._concurrency)
        return self._semaphore

    def resolve_model(self, requested: str | None) -> str:
        if not requested or requested not in AGY_MODELS:
            # For Gemini API / Vertex SDK, strip -low/-medium/-high suffix if needed
            base = self.default_model
            for suffix in ("-low", "-medium", "-high"):
                if base.endswith(suffix) and base.startswith("gemini-"):
                    return base[: -len(suffix)]
            return base
        return requested

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        effective_system, user_prompt, _ = build_combined_prompt(
            messages, response_format
        )
        target_model = self.resolve_model(model)

        response_schema = None
        if response_format and response_format.get("type") == "json_schema":
            schema_obj = response_format.get("json_schema", {}).get("schema")
            if schema_obj:
                response_schema = json.dumps(schema_obj)

        if not SDK_AVAILABLE:
            raise RuntimeError(
                "google-antigravity is not installed; use --backend agy or `pip install google-antigravity`"
            )
        config = LocalAgentConfig(
            model=target_model,
            system_instructions=CustomSystemInstructions(text=effective_system),
            capabilities=CapabilitiesConfig(
                enable_subagents=False,
                enabled_tools=[],
            ),
            workspaces=[str(self.workspace_dir)],
            response_schema=response_schema,
        )

        async with self.semaphore:
            async with Agent(config) as agent:
                response = await agent.chat(user_prompt or " ")
                async for token in response:
                    if token:
                        yield token


class AgyStreamWorker:
    """A pre-warmed persistent `agy` process in `stream-json` mode."""

    def __init__(self, agy_bin: str, workspace_dir: pathlib.Path, model: str, effort: str):
        self.agy_bin = agy_bin
        self.workspace_dir = workspace_dir
        self.model = model
        self.effort = effort
        self.proc: asyncio.subprocess.Process | None = None
        self.turns = 0
        self.ready = False

    async def start(self) -> None:
        cmd = [
            self.agy_bin,
            "--agent",
            "gkey-raw",
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--disable-slash-commands",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
        ]
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.workspace_dir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        assert self.proc.stdout is not None
        # Wait for the `{"event":"init", ...}` line so the worker is 100% hot
        try:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=START_TIMEOUT)
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError(f"agy worker did not initialise within {START_TIMEOUT:.0f}s")
        if not line:
            stderr_bytes = await self.proc.stderr.read() if self.proc.stderr else b""
            await self.close()
            raise RuntimeError(
                f"agy worker exited before init: {stderr_bytes.decode('utf-8', 'replace')[:500]}"
            )
        self.ready = True

    async def close(self) -> None:
        self.ready = False
        if self.proc is not None:
            try:
                if self.proc.stdin and not self.proc.stdin.is_closing():
                    self.proc.stdin.close()
                await asyncio.wait_for(self.proc.wait(), timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
            self.proc = None


class AgyCLIBackend:
    """Backend using a warm pool of `agy --agent gkey-raw --input-format stream-json` workers."""

    def __init__(
        self,
        default_model: str,
        effort: str = "low",
        pool_size: int = 2,
        max_turns_per_worker: int = 30,
    ):
        self.agy_bin = shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")
        self.default_model = default_model
        self.effort = effort
        self.pool_size = pool_size
        self.max_turns_per_worker = max_turns_per_worker
        self.workspace_dir = workspace_root() / "agy"
        self._setup_isolated_workspace()
        self._pool: Optional[asyncio.Queue] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._lock: Optional[asyncio.Lock] = None
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self.last_warm_error: Optional[str] = None

    # asyncio primitives are created lazily (inside the running loop); see SDKBackend.semaphore.
    @property
    def pool(self) -> "asyncio.Queue[AgyStreamWorker]":
        if self._pool is None:
            self._pool = asyncio.Queue()
        return self._pool

    @property
    def semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.pool_size)
        return self._semaphore

    @property
    def _spawn_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _setup_isolated_workspace(self) -> None:
        agents_dir = self.workspace_dir / ".agents" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agents_dir / "gkey-raw.md"
        agent_file.write_text(
            "---\n"
            "name: gkey-raw\n"
            "description: Stateless raw LLM API responder for OpenAI proxy.\n"
            "tools: []\n"
            "---\n"
            f"{DEFAULT_SYSTEM_PROMPT}\n"
            "Each user message marked [STATELESS_API_REQUEST] is a completely independent API call. "
            "Never reference previous turns unless explicitly included inside the current request.\n",
            encoding="utf-8",
        )

    def resolve_model(self, requested: str | None) -> str:
        if requested and requested in AGY_MODELS:
            return requested
        return self.default_model

    async def start_pool(self) -> None:
        logger.info(
            "Pre-warming %d agy worker(s) (model=%s, effort=%s)...",
            self.pool_size,
            self.default_model,
            self.effort,
        )
        for _ in range(self.pool_size):
            self._schedule_replenish()

    def _schedule_replenish(self) -> None:
        if self._closed:
            return
        task = asyncio.create_task(self._create_and_enqueue_worker())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _create_and_enqueue_worker(self) -> None:
        """Background warm-up. Failures are recorded; the next request surfaces them."""
        try:
            async with self._spawn_lock:
                if self._closed:
                    return
                worker = AgyStreamWorker(
                    self.agy_bin, self.workspace_dir, self.default_model, self.effort
                )
                await worker.start()
            if self._closed:
                await worker.close()
                return
            self.last_warm_error = None
            logger.info("agy worker ready (warm=%d)", self.pool.qsize() + 1)
            await self.pool.put(worker)
        except Exception as e:
            self.last_warm_error = str(e)
            logger.warning("Failed to pre-warm agy worker: %s", e)

    async def _acquire_worker(self, model: str) -> "AgyStreamWorker":
        """Take a healthy warm worker, or start one directly.

        Never waits on the warm queue: if warm-up failed, starting a worker here
        raises the real error to the caller instead of hanging forever.
        """
        if model == self.default_model:
            while not self.pool.empty():
                worker = self.pool.get_nowait()
                if worker.proc and worker.proc.returncode is None and worker.ready:
                    return worker
                await worker.close()
        worker = AgyStreamWorker(self.agy_bin, self.workspace_dir, model, self.effort)
        await worker.start()
        return worker

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        _, _, combined_prompt = build_combined_prompt(messages, response_format)
        target_model = self.resolve_model(model)

        async with self.semaphore:
            worker = await self._acquire_worker(target_model)
            recycle = (
                target_model != self.default_model
                or (worker.turns + 1) >= self.max_turns_per_worker
            )

            try:
                assert worker.proc is not None
                assert worker.proc.stdin is not None
                assert worker.proc.stdout is not None

                if worker.turns > 0:
                    request_content = (
                        "[STATELESS_API_REQUEST — Ignore all prior turns in this session.]\n\n"
                        + (combined_prompt or " ")
                    )
                else:
                    request_content = combined_prompt or " "

                payload = json.dumps(
                    {
                        "event": "user",
                        "message": {"content": request_content},
                    },
                    ensure_ascii=False,
                )
                worker.proc.stdin.write((payload + "\n").encode("utf-8"))
                await worker.proc.stdin.drain()
                worker.turns += 1

                yielded_any = False
                got_result = False
                deadline = time.monotonic() + REQUEST_TIMEOUT
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"agy request exceeded {REQUEST_TIMEOUT:.0f}s deadline")
                    try:
                        line = await asyncio.wait_for(worker.proc.stdout.readline(), timeout=remaining)
                    except asyncio.TimeoutError:
                        raise TimeoutError(f"agy request exceeded {REQUEST_TIMEOUT:.0f}s deadline")
                    if not line:
                        # EOF before a result event: the worker died. This is an error, not success.
                        stderr_tail = b""
                        if worker.proc.stderr is not None:
                            try:
                                stderr_tail = await asyncio.wait_for(worker.proc.stderr.read(2000), timeout=0.5)
                            except Exception:
                                pass
                        raise RuntimeError(
                            "agy worker closed its output before returning a result"
                            + (f": {stderr_tail.decode('utf-8', 'replace').strip()}" if stderr_tail else "")
                        )
                    try:
                        ev = json.loads(line.decode("utf-8", "replace"))
                    except json.JSONDecodeError:
                        continue

                    ev_type = ev.get("event")
                    if ev_type == "step_update":
                        su = ev.get("step_update", {})
                        if su.get("step_type") == "agent_response":
                            delta = su.get("text_delta", "")
                            if delta:
                                yielded_any = True
                                yield delta
                    elif ev_type == "result":
                        res = ev.get("result", {})
                        if res.get("status") == "ERROR":
                            raise RuntimeError(res.get("error", "Unknown agy error"))
                        if not yielded_any and res.get("response"):
                            yield res["response"]
                        got_result = True
                        break
                if not got_result:  # pragma: no cover - loop only exits via break or raise
                    raise RuntimeError("agy returned no result")
            except Exception:
                recycle = True
                raise
            finally:
                if recycle:
                    asyncio.create_task(worker.close())
                    if target_model == self.default_model:
                        self._schedule_replenish()
                else:
                    await self.pool.put(worker)

    async def shutdown(self) -> None:
        self._closed = True
        while not self.pool.empty():
            w = await self.pool.get()
            await w.close()


class FakeBackend:
    """Canned-text backend for latency benchmarking: never calls a real model.

    Honours the same pool limit as the real backends (a semaphore of ``pool_size``) so the
    measured queueing matches what a real run would see, minus the model time itself.
    """

    CANNED = ["OK", " - ", "this is ", "canned ", "text."]

    def __init__(self, delay_ms: float = 20.0, pool_size: int = 4) -> None:
        self.delay = max(0.0, delay_ms) / 1000.0
        self.pool_size = pool_size
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def stream_chat(self, messages: list[dict[str, Any]], model: str, response_format: Any = None) -> AsyncIterator[str]:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.pool_size)
        async with self._semaphore:
            await asyncio.sleep(self.delay)
            for piece in self.CANNED:
                yield piece


def has_sdk_credentials() -> bool:
    """Checks whether GEMINI_API_KEY or Vertex AI credentials are set for the Python SDK."""
    if not SDK_AVAILABLE:
        return False
    if os.environ.get("GEMINI_API_KEY"):
        return True
    if os.environ.get("GOOGLE_CLOUD_PROJECT") and os.environ.get("GOOGLE_CLOUD_LOCATION"):
        return True
    return False


def create_app(
    backend_mode: str,
    default_model: str,
    effort: str,
    pool_size: int,
    max_turns_per_worker: int,
    fake_backend: bool = False,
    fake_delay_ms: float = 20.0,
    cache_enabled: bool = False,
    cache_disk: bool = False,
    retries: int = 0,
) -> Starlette:
    retries = max(0, min(int(retries), 10))
    cache: Optional[ResponseCache] = None
    if cache_enabled:
        cache = ResponseCache(
            max_entries=int(os.environ.get("GKEY_CACHE_MAX_ENTRIES", "512")),
            max_bytes=int(os.environ.get("GKEY_CACHE_MAX_BYTES", str(32 * 1024 * 1024))),
            disk_dir=(gkey_home() / "cache") if cache_disk else None,
            max_disk_bytes=int(os.environ.get("GKEY_CACHE_MAX_DISK_BYTES", str(256 * 1024 * 1024))),
        )
    inflight_tasks: dict[str, "asyncio.Task[str]"] = {}
    load = {"inflight": 0, "queue_max": 0, "retried": 0}
    fake = FakeBackend(delay_ms=fake_delay_ms, pool_size=pool_size) if fake_backend else None
    sdk_backend = SDKBackend(default_model=default_model, concurrency=pool_size)
    agy_backend = AgyCLIBackend(
        default_model=default_model,
        effort=effort,
        pool_size=pool_size,
        max_turns_per_worker=max_turns_per_worker,
    )

    def get_active_backend() -> tuple[str, Any]:
        if fake is not None:
            return "fake", fake
        if backend_mode == "sdk":
            if not SDK_AVAILABLE:
                raise RuntimeError("--backend sdk requires the google-antigravity package")
            return "sdk", sdk_backend
        if backend_mode == "agy":
            return "agy", agy_backend
        # auto mode: use SDK if GEMINI_API_KEY / Vertex credentials exist, else agy OAuth
        if has_sdk_credentials():
            return "sdk", sdk_backend
        return "agy", agy_backend

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        name, _ = get_active_backend()
        logger.info(
            "Starting gkey OpenAI proxy (mode=%s, active_engine=%s, default_model=%s)",
            backend_mode,
            name,
            default_model,
        )
        if name == "agy":
            await agy_backend.start_pool()
        try:
            yield
        finally:
            await agy_backend.shutdown()

    async def health(request: Request) -> JSONResponse:
        name, _ = get_active_backend()
        return JSONResponse(
            {
                "status": "ok",
                "backend_mode": backend_mode,
                "active_engine": name,
                "default_model": default_model,
                "effort": effort,
                "pool_size": pool_size,
                "sdk_available": SDK_AVAILABLE,
                "warm_workers": agy_backend.pool.qsize() if name == "agy" else 0,
                "fake_backend": fake is not None,
                "last_warm_error": agy_backend.last_warm_error,
                "retries": retries,
                "retried_requests": load["retried"],
                "inflight": load["inflight"],
                "queue_max": load["queue_max"],
                **(cache.stats() if cache is not None else {"cache_enabled": False}),
            }
        )

    async def list_models(request: Request) -> JSONResponse:
        now = int(time.time())
        models_list = sorted(AGY_MODELS) + ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": m,
                        "object": "model",
                        "created": now,
                        "owned_by": "antigravity",
                    }
                    for m in models_list
                ],
            }
        )

    async def chat_completions(request: Request) -> Any:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": {"message": "Invalid JSON request body", "type": "invalid_request_error"}},
                status_code=400,
            )

        messages = body.get("messages", [])
        if not messages:
            return JSONResponse(
                {"error": {"message": "messages is required", "type": "invalid_request_error"}},
                status_code=400,
            )

        req_model = body.get("model") or default_model
        stream = bool(body.get("stream", False))
        response_format = body.get("response_format")

        engine_name, backend = get_active_backend()
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        t_start = time.perf_counter()
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)

        load["inflight"] += 1
        queued = max(0, load["inflight"] - pool_size)
        load["queue_max"] = max(load["queue_max"], queued)
        if queued:
            logger.info("QUEUE depth=%d inflight=%d pool=%d", queued, load["inflight"], pool_size)
        released = [False]

        def release() -> None:
            if not released[0]:
                released[0] = True
                load["inflight"] -= 1

        cache_state = ["-"]

        def log_req(status: str, ttft: Optional[float], out_chars: int) -> None:
            # One machine-parsable line per request: `gkey --logs` / bench scripts read these.
            logger.info(
                "REQ id=%s engine=%s stream=%d status=%s in_chars=%d out_chars=%d ttft_ms=%s total_ms=%.0f queued=%d cache=%s",
                completion_id[-8:], engine_name, int(stream), status, prompt_chars, out_chars,
                "-" if ttft is None else f"{ttft * 1000:.0f}", (time.perf_counter() - t_start) * 1000,
                queued, cache_state[0],
            )

        def backend_stream() -> AsyncIterator[str]:
            def note_retry() -> None:
                load["retried"] += 1

            return retrying_stream(
                backend, retries, on_retry=note_retry,
                messages=messages, model=req_model, response_format=response_format,
            )

        if stream:
            async def sse_generator() -> AsyncIterator[bytes]:
                first_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": req_model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": ""},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n".encode("utf-8")

                ttft: Optional[float] = None
                n_out = 0
                try:
                    async for delta in backend_stream():
                        if ttft is None:
                            ttft = time.perf_counter() - t_start
                        n_out += len(delta)
                        chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": req_model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": delta},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                except Exception as e:
                    logger.exception("Error during streaming chat completion (%s)", engine_name)
                    log_req("error", ttft, n_out)
                    err_chunk = {
                        "error": {
                            "message": str(e),
                            "type": "server_error",
                        }
                    }
                    release()
                    yield f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                    yield b"data: [DONE]\n\n"
                    return
                finally:
                    release()

                stop_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": req_model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],
                }
                log_req("ok", ttft, n_out)
                yield f"data: {json.dumps(stop_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"

            return StreamingResponse(
                sse_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        # Non-streaming response
        ttft_box: list[Optional[float]] = [None]

        async def generate_text() -> str:
            chunks: list[str] = []
            async for delta in backend_stream():
                if ttft_box[0] is None:
                    ttft_box[0] = time.perf_counter() - t_start
                chunks.append(delta)
            return "".join(chunks).strip()

        try:
            if cache is None:
                full_text = await generate_text()
            else:
                key = cache_key(body, f"{engine_name}|{default_model}|{effort}")
                hit = cache.get(key)
                if hit is not None:
                    cache.hits += 1
                    cache_state[0] = "hit"
                    full_text = hit
                else:
                    task = inflight_tasks.get(key)
                    if task is not None:
                        cache.coalesced += 1
                        cache_state[0] = "coalesced"
                    else:
                        cache.misses += 1
                        cache_state[0] = "miss"

                        async def compute() -> str:
                            text = await generate_text()
                            cache.put(key, text)  # only reached on success: errors are never cached
                            return text

                        task = asyncio.create_task(compute())
                        inflight_tasks[key] = task

                        def _done(t: "asyncio.Task[str]", key: str = key) -> None:
                            if inflight_tasks.get(key) is t:
                                del inflight_tasks[key]
                            if not t.cancelled():
                                t.exception()  # mark retrieved even if every waiter went away

                        task.add_done_callback(_done)
                    full_text = await asyncio.shield(task)
            log_req("ok", ttft_box[0], len(full_text))
        except Exception as e:
            logger.exception("Error during chat completion (%s)", engine_name)
            log_req("error", None, 0)
            return JSONResponse(
                {
                    "error": {
                        "message": str(e),
                        "type": "server_error",
                        "engine": engine_name,
                    }
                },
                status_code=500,
            )
        finally:
            release()

        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": req_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": full_text,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }
        )

    async def audio_speech(request: Request) -> Response:
        """OpenAI-compatible /v1/audio/speech TTS endpoint (for epub_to_audiobook, etc.)."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "Invalid JSON"}}, status_code=400)

        input_text = str(body.get("input", "")).strip()
        if not input_text:
            return JSONResponse({"error": {"message": "input is required"}}, status_code=400)

        if not shutil.which("say") or not shutil.which("afconvert"):
            return JSONResponse(
                {"error": {"message": "/v1/audio/speech is only available on macOS (needs `say`)"}},
                status_code=501,
            )

        with tempfile.TemporaryDirectory(prefix="gkey_tts_") as tmpdir:
            aiff_path = os.path.join(tmpdir, "out.aiff")
            m4a_path = os.path.join(tmpdir, "out.m4a")
            proc = await asyncio.create_subprocess_exec(
                "say", "-o", aiff_path, input_text
            )
            if await proc.wait() != 0:
                return JSONResponse({"error": {"message": "`say` failed"}}, status_code=500)
            conv = await asyncio.create_subprocess_exec(
                "afconvert", "-f", "mp4f", "-d", "aac", aiff_path, m4a_path
            )
            await conv.wait()
            out_file = m4a_path if os.path.exists(m4a_path) else aiff_path
            audio_bytes = pathlib.Path(out_file).read_bytes()
            return Response(content=audio_bytes, media_type="audio/mpeg")

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/v1/models", list_models, methods=["GET"]),
        Route("/models", list_models, methods=["GET"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/audio/speech", audio_speech, methods=["POST"]),
        Route("/audio/speech", audio_speech, methods=["POST"]),
    ]

    return Starlette(
        routes=routes,
        lifespan=lifespan,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="gkey OpenAI-compatible Antigravity Proxy")
    parser.add_argument("--host", default=os.environ.get("GKEY_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("GKEY_PORT", "18432"))
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "sdk", "agy"],
        default=os.environ.get("GKEY_BACKEND", "auto"),
        help="Backend engine: 'sdk' (google-antigravity Python SDK), 'agy' (agy CLI OAuth pool), or 'auto'",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GKEY_MODEL", "gemini-3.8-flash-low"),
        help="Default model when client specifies an unknown/OpenAI model name",
    )
    parser.add_argument(
        "--effort",
        default=os.environ.get("GKEY_EFFORT", "low"),
        choices=["low", "medium", "high", "xhigh", "max"],
        help="Reasoning effort for agy worker pool",
    )
    parser.add_argument(
        "--pool-size",
        type=int,
        default=int(os.environ.get("GKEY_POOL_SIZE", "4")),
        help=f"Number of warm workers / concurrent requests (1-{MAX_POOL_SIZE})",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=int(os.environ.get("GKEY_MAX_TURNS", "6")),
        help="Max turns per warm agy worker before recycling",
    )
    parser.add_argument(
        "--fake-backend",
        action="store_true",
        default=os.environ.get("GKEY_FAKE_BACKEND", "") not in ("", "0", "false"),
        help="Benchmark mode: reply with canned text after a small delay; never calls a model",
    )
    parser.add_argument(
        "--fake-delay-ms",
        type=float,
        default=float(os.environ.get("GKEY_FAKE_DELAY_MS", "20")),
        help="Simulated per-request model delay in fake-backend mode (default 20)",
    )
    parser.add_argument(
        "--cache",
        action="store_true",
        default=os.environ.get("GKEY_CACHE", "") not in ("", "0", "false"),
        help="Opt-in response cache (+ coalescing of identical in-flight requests) for non-streaming calls",
    )
    parser.add_argument(
        "--cache-disk",
        action="store_true",
        default=os.environ.get("GKEY_CACHE_DISK", "") not in ("", "0", "false"),
        help="With --cache: also persist responses under GKEY_HOME/cache (size bounded)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=int(os.environ.get("GKEY_RETRIES", "0")),
        help="Retries (exponential backoff + jitter) on transient backend failures (default 0)",
    )
    args = parser.parse_args()
    args.pool_size = max(1, min(args.pool_size, MAX_POOL_SIZE))
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Binding to %s exposes an UNAUTHENTICATED proxy beyond this machine. "
            "Anyone who can reach it can spend your Antigravity quota.",
            args.host,
        )

    app = create_app(
        backend_mode=args.backend,
        default_model=args.model,
        effort=args.effort,
        pool_size=args.pool_size,
        max_turns_per_worker=args.max_turns,
        fake_backend=args.fake_backend,
        fake_delay_ms=args.fake_delay_ms,
        cache_enabled=args.cache,
        cache_disk=args.cache_disk,
        retries=args.retries,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
