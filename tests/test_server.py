import asyncio
import os
import stat

import pytest

pytest.importorskip("starlette")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("GKEY_HOME", str(tmp_path))
    import server as srv

    return srv


def fake_agy(tmp_path, body):
    p = tmp_path / "agy"
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def make_backend(server, agy_bin):
    b = server.AgyCLIBackend("gemini-3.8-flash-low", pool_size=2)
    b.agy_bin = agy_bin
    return b


async def settle(backend):
    """Let background warm-ups/closes finish before asyncio.run() tears the loop down.

    Cancelling a replenish task while `create_subprocess_exec` is still starting the child makes
    asyncio wait for that child to exit (CPython behaviour), and the fake agy blocks on stdin, so
    asyncio.run()'s cancel-all step hangs. Production never tears the loop down mid-spawn.
    """
    await asyncio.gather(*list(getattr(backend, "_bg_tasks", ())), return_exceptions=True)
    await asyncio.sleep(0.3)
    await backend.shutdown()


async def collect(backend):
    try:
        return [d async for d in backend.stream_chat([{"role": "user", "content": "hi"}])]
    finally:
        await settle(backend)


def test_happy_path(server, tmp_path):
    agy = fake_agy(tmp_path, '''echo '{"event":"init"}'
while read -r line; do
  echo '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"ok"}}'
  echo '{"event":"result","result":{"status":"OK"}}'
done
''')
    assert asyncio.run(collect(make_backend(server, agy))) == ["ok"]


def test_eof_before_result_is_an_error(server, tmp_path):
    agy = fake_agy(tmp_path, '''echo '{"event":"init"}'
read -r line
exit 0
''')
    with pytest.raises(RuntimeError, match="closed its output"):
        asyncio.run(collect(make_backend(server, agy)))


def test_worker_start_failure_propagates_instead_of_hanging(server, tmp_path):
    agy = fake_agy(tmp_path, "echo boom >&2\nexit 3\n")

    async def go():
        b = make_backend(server, agy)
        await b.start_pool()
        await asyncio.sleep(0.5)  # let warm-up fail
        return await asyncio.wait_for(collect(b), timeout=10)

    with pytest.raises(RuntimeError, match="exited before init"):
        asyncio.run(go())


def test_request_deadline(server, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REQUEST_TIMEOUT", 0.5)
    agy = fake_agy(tmp_path, '''echo '{"event":"init"}'
read -r line
sleep 30
''')
    with pytest.raises(TimeoutError):
        asyncio.run(collect(make_backend(server, agy)))


def test_health_reports_config(server):
    from starlette.testclient import TestClient

    try:
        import httpx  # noqa: F401
    except ImportError:
        pytest.skip("httpx not installed")
    app = server.create_app("agy", "gemini-3.8-flash-low", "high", 6, 6)
    with TestClient(app) as c:
        h = c.get("/health").json()
    assert h["pool_size"] == 6 and h["effort"] == "high" and h["backend_mode"] == "agy"


def test_fake_backend_nonstream_and_stream(server):
    from starlette.testclient import TestClient

    app = server.create_app("auto", "m", "low", 2, 6, fake_backend=True, fake_delay_ms=1)
    with TestClient(app) as c:
        assert c.get("/health").json()["fake_backend"] is True
        r = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"].startswith("OK")
        r = c.post("/v1/chat/completions", json={"stream": True, "messages": [{"role": "user", "content": "x"}]})
        assert "data: [DONE]" in r.text and "canned" in r.text


@pytest.mark.parametrize("stream", [False, True])
def test_request_log_line(server, caplog, stream):
    from starlette.testclient import TestClient

    app = server.create_app("auto", "gemini-3.8-flash-low", "low", 1, 10, fake_backend=True, fake_delay_ms=1)
    with caplog.at_level("INFO", logger="gkey-proxy"), TestClient(app) as c:
        r = c.post("/v1/chat/completions", json={"model": "x", "stream": stream, "messages": [{"role": "user", "content": "hello"}]})
        assert r.status_code == 200
    lines = [m for m in caplog.messages if m.startswith("REQ ")]
    assert len(lines) == 1
    assert f"stream={int(stream)}" in lines[0] and "status=ok" in lines[0] and "in_chars=5" in lines[0]


# ---- speed features: retries, cache, coalescing ---------------------------------------------

def test_is_transient_error(server):
    assert server.is_transient_error(RuntimeError("agy worker closed its output before returning a result"))
    assert server.is_transient_error(RuntimeError("HTTP 503 unavailable"))
    assert server.is_transient_error(ConnectionResetError())
    assert not server.is_transient_error(RuntimeError("invalid api key"))
    assert not server.is_transient_error(TimeoutError("agy request exceeded 300s deadline"))
    assert not server.is_transient_error(ValueError("bad"))


def test_backoff_grows_and_is_capped(server):
    for attempt in range(8):
        d = server.backoff_delay(attempt)
        assert 0 < d <= server.RETRY_MAX_DELAY
    assert server.backoff_delay(0) <= 0.5 and server.backoff_delay(3) >= 2.0


class FlakyBackend:
    def __init__(self, fail_times, exc=RuntimeError("closed its output"), partial=False):
        self.calls = 0
        self.fail_times = fail_times
        self.exc = exc
        self.partial = partial

    async def stream_chat(self, messages, model=None, response_format=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            if self.partial:
                yield "par"
            raise self.exc
        yield "ok"


def run_retrying(server, backend, retries, monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(server, "_sleep", fake_sleep)

    async def go():
        return [d async for d in server.retrying_stream(backend, retries, messages=[], model="m")]

    return go, slept


def test_retry_recovers(server, monkeypatch):
    b = FlakyBackend(2)
    go, slept = run_retrying(server, b, 2, monkeypatch)
    assert asyncio.run(go()) == ["ok"] and b.calls == 3 and len(slept) == 2 and slept[1] > 0


def test_retry_exhausted_and_non_transient(server, monkeypatch):
    b = FlakyBackend(5)
    go, _ = run_retrying(server, b, 1, monkeypatch)
    with pytest.raises(RuntimeError):
        asyncio.run(go())
    assert b.calls == 2
    b = FlakyBackend(5, exc=RuntimeError("invalid request"))
    go, _ = run_retrying(server, b, 3, monkeypatch)
    with pytest.raises(RuntimeError):
        asyncio.run(go())
    assert b.calls == 1


def test_no_retry_after_partial_output(server, monkeypatch):
    b = FlakyBackend(5, partial=True)
    go, _ = run_retrying(server, b, 3, monkeypatch)
    with pytest.raises(RuntimeError):
        asyncio.run(go())
    assert b.calls == 1


def test_cache_lru_bounds_and_no_empty(server):
    c = server.ResponseCache(max_entries=2)
    c.put("a", "1"); c.put("b", "2"); c.get("a"); c.put("c", "3")
    assert c.get("b") is None and c.get("a") == "1" and c.get("c") == "3"
    c.put("e", "  ")
    assert c.get("e") is None
    small = server.ResponseCache(max_bytes=100)
    small.put("big", "x" * 200)  # larger than half the budget: not stored
    assert small.get("big") is None


def test_cache_disk_roundtrip_and_trim(server, tmp_path):
    d = tmp_path / "cache"
    c = server.ResponseCache(disk_dir=d)
    c.put("k", "hello")
    fresh = server.ResponseCache(disk_dir=d)  # new process: memory empty
    assert fresh.get("k") == "hello"
    t = server.ResponseCache(disk_dir=tmp_path / "c2", max_disk_bytes=400)
    for i in range(20):
        t.put(f"k{i}", "y" * 90)
    assert sum(p.stat().st_size for p in (tmp_path / "c2").glob("*.txt")) <= 400


def test_cache_key_ignores_stream_but_not_params(server):
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    k = server.cache_key(base, "ctx")
    assert k == server.cache_key({**base, "stream": False, "user": "u"}, "ctx")
    assert k != server.cache_key({**base, "temperature": 0.7}, "ctx")
    assert k != server.cache_key({**base, "model": "n"}, "ctx")
    assert k != server.cache_key(base, "other-ctx")


def _fake_app(server, monkeypatch, backend_cls, **kw):
    monkeypatch.setattr(server, "FakeBackend", backend_cls)
    return server.create_app("auto", "m", "low", kw.pop("pool", 2), 6, fake_backend=True, fake_delay_ms=1, **kw)


class Counting:
    calls = 0
    delay = 0.0
    fail = 0

    def __init__(self, delay_ms=0, pool_size=1):
        pass

    async def stream_chat(self, messages, model=None, response_format=None):
        type(self).calls += 1
        n = type(self).calls
        await asyncio.sleep(type(self).delay)
        if n <= type(self).fail:
            raise RuntimeError("closed its output")
        yield "answer"


def _counting():
    class C(Counting):
        calls = 0
        delay = 0.0
        fail = 0
    return C


def test_cache_hit_and_stats(server, monkeypatch):
    from starlette.testclient import TestClient
    pytest.importorskip("httpx")
    C = _counting()
    app = _fake_app(server, monkeypatch, C, cache_enabled=True)
    body = {"model": "x", "messages": [{"role": "user", "content": "same"}]}
    with TestClient(app) as c:
        assert c.get("/health").json()["cache_enabled"] is True
        r1 = c.post("/v1/chat/completions", json=body)
        r2 = c.post("/v1/chat/completions", json=body)
        r3 = c.post("/v1/chat/completions", json={**body, "temperature": 0.5})
        rs = c.post("/v1/chat/completions", json={**body, "stream": True})  # streaming bypasses the cache
        h = c.get("/health").json()
    assert r1.json()["choices"][0]["message"]["content"] == r2.json()["choices"][0]["message"]["content"] == "answer"
    assert r1.json()["id"] != r2.json()["id"]
    assert C.calls == 3  # r1, r3, stream
    assert "answer" in rs.text
    assert h["cache_hits"] == 1 and h["cache_misses"] == 2 and h["cache_hit_rate"] == 0.333


def test_cache_off_by_default(server, monkeypatch):
    from starlette.testclient import TestClient
    pytest.importorskip("httpx")
    C = _counting()
    app = _fake_app(server, monkeypatch, C)
    body = {"messages": [{"role": "user", "content": "same"}]}
    with TestClient(app) as c:
        c.post("/v1/chat/completions", json=body)
        c.post("/v1/chat/completions", json=body)
        assert c.get("/health").json()["cache_enabled"] is False
    assert C.calls == 2


def test_errors_never_cached_and_retry_applies(server, monkeypatch):
    from starlette.testclient import TestClient
    pytest.importorskip("httpx")

    async def nosleep(s):
        pass

    monkeypatch.setattr(server, "_sleep", nosleep)
    C = _counting(); C.fail = 1
    app = _fake_app(server, monkeypatch, C, cache_enabled=True)  # retries=0
    body = {"messages": [{"role": "user", "content": "q"}]}
    with TestClient(app) as c:
        assert c.post("/v1/chat/completions", json=body).status_code == 500
        assert c.post("/v1/chat/completions", json=body).status_code == 200  # error was not cached
    C = _counting(); C.fail = 2
    app = _fake_app(server, monkeypatch, C, retries=2)
    with TestClient(app) as c:
        assert c.post("/v1/chat/completions", json=body).status_code == 200
        assert c.get("/health").json()["retried_requests"] == 2
    assert C.calls == 3


def test_coalescing_identical_inflight(server, monkeypatch):
    httpx = pytest.importorskip("httpx")
    C = _counting(); C.delay = 0.3
    app = _fake_app(server, monkeypatch, C, cache_enabled=True, pool=4)
    body = {"messages": [{"role": "user", "content": "dup"}]}

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            rs = await asyncio.gather(*[c.post("/v1/chat/completions", json=body) for _ in range(4)])
            return rs, (await c.get("/health")).json()

    rs, h = asyncio.run(go())
    assert all(r.status_code == 200 for r in rs)
    assert C.calls == 1 and h["cache_coalesced"] == 3 and h["cache_misses"] == 1


def test_queue_depth_logged(server, monkeypatch, caplog):
    httpx = pytest.importorskip("httpx")
    C = _counting(); C.delay = 0.2
    app = _fake_app(server, monkeypatch, C, pool=1)

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            await asyncio.gather(*[c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": str(i)}]}) for i in range(3)])
            return (await c.get("/health")).json()

    with caplog.at_level("INFO", logger="gkey-proxy"):
        h = asyncio.run(go())
    assert any(m.startswith("QUEUE depth=") for m in caplog.messages)
    assert h["queue_max"] >= 1 and h["inflight"] == 0
