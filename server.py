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
import json
import logging
import os
import pathlib
import shutil
import tempfile
import time
import uuid
from typing import Any, AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
import uvicorn

# Official Google Antigravity Python SDK imports
import google.antigravity as ag
from google.antigravity import (
    Agent,
    CapabilitiesConfig,
    CustomSystemInstructions,
    LocalAgentConfig,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("gkey-proxy")

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


def format_openai_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Extracts system instructions and formats conversation messages into a prompt."""
    system_parts: list[str] = []
    conv_parts: list[str] = []
    img_dir = pathlib.Path("/tmp/gkey-agy-workspace")
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
        self.semaphore = asyncio.Semaphore(concurrency)
        self.workspace_dir = pathlib.Path("/tmp/gkey-sdk-workspace")
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

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
        line = await self.proc.stdout.readline()
        if not line:
            stderr_bytes = await self.proc.stderr.read() if self.proc.stderr else b""
            raise RuntimeError(
                f"agy worker exited before init: {stderr_bytes.decode('utf-8', 'replace')}"
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
        self.workspace_dir = pathlib.Path("/tmp/gkey-agy-workspace")
        self._setup_isolated_workspace()
        self.pool: asyncio.Queue[AgyStreamWorker] = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(pool_size)
        self._spawn_lock = asyncio.Lock()
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

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
            logger.info("agy worker ready (pool_size=%d)", self.pool.qsize() + 1)
            await self.pool.put(worker)
        except Exception as e:
            logger.warning("Failed to pre-warm agy worker: %s", e)

    async def _acquire_worker(self, model: str) -> AgyStreamWorker:
        if model == self.default_model:
            while not self.pool.empty():
                worker = await self.pool.get()
                if worker.proc and worker.proc.returncode is None and worker.ready:
                    return worker
                await worker.close()

            if not self._bg_tasks:
                self._schedule_replenish()

            worker = await self.pool.get()
            if worker.proc and worker.proc.returncode is None and worker.ready:
                return worker
            await worker.close()

        worker = AgyStreamWorker(
            self.agy_bin, self.workspace_dir, model, self.effort
        )
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
            if recycle and target_model == self.default_model:
                self._schedule_replenish()

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
                while True:
                    line = await worker.proc.stdout.readline()
                    if not line:
                        break
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
                            err_msg = res.get("error", "Unknown agy error")
                            raise RuntimeError(err_msg)
                        if not yielded_any and res.get("response"):
                            yield res["response"]
                        break
            except Exception:
                recycle = True
                raise
            finally:
                if recycle:
                    asyncio.create_task(worker.close())
                else:
                    await self.pool.put(worker)

    async def shutdown(self) -> None:
        self._closed = True
        while not self.pool.empty():
            w = await self.pool.get()
            await w.close()


def has_sdk_credentials() -> bool:
    """Checks whether GEMINI_API_KEY or Vertex AI credentials are set for the Python SDK."""
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
) -> Starlette:
    sdk_backend = SDKBackend(default_model=default_model, concurrency=pool_size)
    agy_backend = AgyCLIBackend(
        default_model=default_model,
        effort=effort,
        pool_size=pool_size,
        max_turns_per_worker=max_turns_per_worker,
    )

    def get_active_backend() -> tuple[str, SDKBackend | AgyCLIBackend]:
        if backend_mode == "sdk":
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
                "warm_workers": agy_backend.pool.qsize() if name == "agy" else 0,
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

                try:
                    async for delta in backend.stream_chat(
                        messages=messages,
                        model=req_model,
                        response_format=response_format,
                    ):
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
                    err_chunk = {
                        "error": {
                            "message": str(e),
                            "type": "server_error",
                        }
                    }
                    yield f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n".encode("utf-8")

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
        try:
            chunks: list[str] = []
            async for delta in backend.stream_chat(
                messages=messages,
                model=req_model,
                response_format=response_format,
            ):
                chunks.append(delta)
            full_text = "".join(chunks).strip()
        except Exception as e:
            logger.exception("Error during chat completion (%s)", engine_name)
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

        with tempfile.TemporaryDirectory(prefix="gkey_tts_") as tmpdir:
            aiff_path = os.path.join(tmpdir, "out.aiff")
            m4a_path = os.path.join(tmpdir, "out.m4a")
            proc = await asyncio.create_subprocess_exec(
                "say", "-o", aiff_path, input_text
            )
            await proc.wait()
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
        help="Number of warm workers / concurrent requests",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=int(os.environ.get("GKEY_MAX_TURNS", "6")),
        help="Max turns per warm agy worker before recycling",
    )
    args = parser.parse_args()

    app = create_app(
        backend_mode=args.backend,
        default_model=args.model,
        effort=args.effort,
        pool_size=args.pool_size,
        max_turns_per_worker=args.max_turns,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
