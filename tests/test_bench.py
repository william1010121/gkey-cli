import pytest

pytest.importorskip("starlette")
import bench


def test_percentile():
    assert bench.percentile([], 50) == 0.0
    assert bench.percentile([1, 2, 3, 4], 50) == 2
    assert bench.percentile([1, 2, 3, 4], 95) == 4
    assert bench.percentile(list(range(1, 101)), 95) == 95


def test_bench_end_to_end(capsys):
    rc = bench.main(["--bench", "--concurrency", "4", "--requests", "12", "--pool-size", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "12/12 ok" in out and "p95" in out and "req/s" in out
    assert "pool size     : 2" in out and "proxy overhead" in out.lower() or "PROXY" in out
