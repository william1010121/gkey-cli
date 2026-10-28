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


async def collect(backend):
    return [d async for d in backend.stream_chat([{"role": "user", "content": "hi"}])]


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
