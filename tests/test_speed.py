import os
import stat
import sys

import pytest

import catalog
import gkey_speed as sp


def test_apply_fast_adds_flags_and_user_wins():
    prof = {"flags": ["-t", "16"], "aliases": {"-t": ["--thread"]}, "env": {"FOO": "1"}}
    env = {}
    args, notes = sp.apply_fast("pdf2zh", ["a.pdf"], prof, env, environ={})
    assert args == ["a.pdf", "-t", "16"] and env == {"FOO": "1"} and notes
    for user in (["a.pdf", "-t", "4"], ["a.pdf", "--thread", "4"], ["a.pdf", "--thread=4"], ["a.pdf", "-t8"]):
        out, _ = sp.apply_fast("pdf2zh", user, prof, {}, environ={})
        assert out == user
    env = {}
    sp.apply_fast("pdf2zh", ["a.pdf"], prof, env, environ={"FOO": "mine"})
    assert env == {}  # user's environment wins


def test_apply_fast_empty_tolerant():
    args, notes = sp.apply_fast("x", ["a"], None, {}, environ={})
    assert args == ["a"] and "no tuned profile" in notes[0]
    args, _ = sp.apply_fast("x", ["a"], {}, {}, environ={})
    assert args == ["a"]


def test_apply_batch():
    assert sp.apply_batch(["a"], 8, "--maxbatchsize")[0] == ["a", "--maxbatchsize", "8"]
    assert sp.apply_batch(["a", "--maxbatchsize", "2"], 8, "--maxbatchsize")[0] == ["a", "--maxbatchsize", "2"]
    out, note = sp.apply_batch(["a"], 8, None)
    assert out == ["a"] and "ignored" in note
    assert sp.apply_batch(["a"], None, "--x") == (["a"], None)


def test_pdf2zh_profile_shipped():
    spec = catalog.TOOL_BY_ID["pdf2zh"]
    prof = sp.tool_config(spec, "fast", catalog.SPEED)
    assert prof["flags"] == ["-t", "16"] and prof["pool"] == 16
    assert sp.tool_config(catalog.TOOL_BY_ID["mods"], "fast", catalog.SPEED) is None  # tools without a profile still work


def test_toolspec_attribute_wins_over_table():
    class S:
        id = "pdf2zh"
        fast = {"flags": ["-t", "2"]}
    assert sp.tool_config(S(), "fast", catalog.SPEED) == {"flags": ["-t", "2"]}


def test_pool_hint(monkeypatch):
    spec = catalog.TOOL_BY_ID["pdf2zh"]
    monkeypatch.delenv("GKEY_FAST", raising=False)
    monkeypatch.delenv("GKEY_PARALLEL_FILES", raising=False)
    assert sp.speed_pool_hint(spec, ["a.pdf"], catalog.SPEED) is None
    monkeypatch.setenv("GKEY_FAST", "1")
    assert sp.speed_pool_hint(spec, ["a.pdf"], catalog.SPEED) == 16
    monkeypatch.delenv("GKEY_FAST")
    monkeypatch.setenv("GKEY_PARALLEL_FILES", "3")
    assert sp.speed_pool_hint(spec, ["a.pdf", "-t", "3"], catalog.SPEED) == 9
    assert sp.speed_pool_hint(spec, ["a.pdf", "-t", "8"], catalog.SPEED) == 16  # capped


def test_find_inputs_and_build_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for n in ("a.pdf", "b.pdf", "out.txt"):
        (tmp_path / n).write_text("x")
    vf = catalog.SPEED["pdf2zh"]["per_file"]["value_flags"]
    args = ["a.pdf", "-o", "out.txt", "b.pdf", "-t", "4"]
    idx = sp.find_inputs(args, vf)
    assert idx == [0, 3]  # out.txt is the value of -o, not an input
    runs = sp.build_runs(args, idx)
    assert runs == [["a.pdf", "-o", "out.txt", "-t", "4"], ["b.pdf", "-o", "out.txt", "-t", "4"]]
    assert sp.unique_labels(args, idx) == ["a.pdf", "b.pdf"]
    # conservative default: without value_flags the token after any option is a value
    assert sp.find_inputs(["--flag", "a.pdf", "b.pdf"]) == [2]


def _script(tmp_path, body):
    p = tmp_path / "tool.sh"
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_run_parallel_prefix_exit_codes_and_concurrency(tmp_path):
    import io
    tool = _script(tmp_path, 'f="$1"; echo "start $f"; sleep 0.3; echo "done $f"; [[ "$f" == bad* ]] && exit 7; exit 0\n')
    out = io.StringIO()
    posts = []
    import time
    t0 = time.time()
    rc = sp.run_parallel(tool, [["a"], ["bad"], ["c"]], ["a", "bad", "c"], 3, post=posts.append, out=out)
    elapsed = time.time() - t0
    text = out.getvalue()
    assert rc == 7
    assert elapsed < 0.85  # concurrent, not 3 x 0.3 s
    assert "[a] start a" in text and "[bad] done bad" in text and "[bad] exit 7" in text
    assert "2/3 succeeded; failed: bad" in text
    assert sorted(map(tuple, posts)) == [("a",), ("c",)]  # post-step only for successful runs


def test_run_parallel_limits_workers(tmp_path):
    import io, time
    tool = _script(tmp_path, "sleep 0.3\n")
    t0 = time.time()
    assert sp.run_parallel(tool, [["1"], ["2"], ["3"], ["4"]], list("1234"), 2, out=io.StringIO()) == 0
    assert time.time() - t0 >= 0.55


def test_run_parallel_missing_binary():
    import io
    out = io.StringIO()
    assert sp.run_parallel("/nonexistent/tool", [["a"]], ["a"], 1, out=out) == 127


def test_maybe_run_parallel_not_applicable(monkeypatch, capsys):
    monkeypatch.setenv("GKEY_PARALLEL_FILES", "3")
    # tool without per_file contract: runs normally with a message
    assert catalog.maybe_run_parallel(catalog.TOOL_BY_ID["mods"], "mods", ["a", "b"], None) is None
    assert "not marked one-file-per-run" in capsys.readouterr().err
    monkeypatch.delenv("GKEY_PARALLEL_FILES")
    assert catalog.maybe_run_parallel(catalog.TOOL_BY_ID["pdf2zh"], "pdf2zh", ["a.pdf", "b.pdf"], "dual") is None


def test_apply_speed_options_env(monkeypatch, capsys):
    monkeypatch.setenv("GKEY_FAST", "1")
    monkeypatch.setenv("GKEY_BATCH", "5")
    spec = catalog.TOOL_BY_ID["pdf2zh"]
    args = catalog.apply_speed_options(spec, ["a.pdf"], {})
    assert args[-2:] == ["-t", "16"]
    err = capsys.readouterr().err
    assert "--fast (pdf2zh)" in err and "--batch (pdf2zh)" in err and "ignored" in err
    assert catalog.apply_speed_options(spec, ["a.pdf", "-t", "2"], {})[-2:] == ["-t", "2"]


def test_options_off_by_default(monkeypatch):
    for k in ("GKEY_FAST", "GKEY_BATCH", "GKEY_PARALLEL_FILES"):
        monkeypatch.delenv(k, raising=False)
    spec = catalog.TOOL_BY_ID["pdf2zh"]
    assert catalog.apply_speed_options(spec, ["a.pdf"], {}) == ["a.pdf"]


def test_info_mentions_speed_options(capsys):
    catalog.print_preview("pdf2zh")
    out = capsys.readouterr().out
    assert "Speed options" in out and "-t 16" in out and "--parallel-files" in out
    catalog.print_preview("mods")
    assert "no tuned profile" in capsys.readouterr().out
