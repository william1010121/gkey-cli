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


def test_parse_config():
    base = {"pool_size": 4, "cache": False, "dup_pct": 0, "concurrency": 4, "requests": 40, "delay_ms": 20.0}
    c = bench.parse_config("pool=16,cache=on,dup=50", base)
    assert c["pool_size"] == 16 and c["cache"] is True and c["dup_pct"] == 50 and c["concurrency"] == 4
    assert bench.parse_config("pool=99", base)["pool_size"] == 16
    with pytest.raises(ValueError):
        bench.parse_config("bogus=1", base)
    with pytest.raises(ValueError):
        bench.parse_config("cache=maybe", base)


def test_prompt_for_duplicates():
    assert bench.prompt_for(3, 0) == ("bench-3", False)
    p, dup = bench.prompt_for(3, 50)
    assert dup and p.startswith("bench-dup-")
    assert not bench.prompt_for(60, 50)[1]


def test_bench_compare_cache(capsys):
    rc = bench.main(["--bench", "--requests", "20", "--concurrency", "4", "--dup-pct", "80",
                     "--compare", "cache=off", "--compare", "cache=on"])
    out = capsys.readouterr().out
    assert rc == 0 and "cache=off" in out and "cache=on" in out and "vs first" in out
    on = [l for l in out.splitlines() if l.strip().startswith("cache=on")][0]
    assert "%" in on and "off" not in on.split()[-3:-1]
