import io
import os
import pathlib
import subprocess
import sys

import catalog
import menu

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_filter_and_resolve():
    ids = [t.id for t in menu.filter_tools("pdf", catalog.TOOLS)]
    assert "pdf2zh" in ids and "oco" not in ids
    assert [t.id for t in menu.filter_tools("", catalog.TOOLS)] == [t.id for t in catalog.TOOLS]
    assert menu.resolve_choice("1", catalog.TOOLS) == catalog.TOOLS[0].id
    assert menu.resolve_choice("999", catalog.TOOLS) is None
    assert menu.resolve_choice("marker", catalog.TOOLS) == "marker_single"  # alias
    assert menu.resolve_choice("fabr", catalog.TOOLS) == "fabric"  # unique substring
    assert menu.resolve_choice("pdf", catalog.TOOLS) is None  # ambiguous


def test_numbered_fallback_lists_every_tool():
    buf = io.StringIO()
    menu.numbered_fallback(catalog.TOOLS, buf)
    text = buf.getvalue()
    for t in catalog.TOOLS:
        assert t.id in text
    assert "gkey info <tool>" in text


def test_non_tty_prints_numbered_list_and_exits():
    res = subprocess.run(
        [sys.executable, str(ROOT / "menu.py")], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, universal_newlines=True, timeout=20,
    )
    assert res.returncode == 0
    assert "  1. pdf2zh" in res.stdout


def test_gkey_without_args_non_tty_falls_back():
    res = subprocess.run(
        ["bash", str(ROOT / "gkey")], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        universal_newlines=True, timeout=20, env=dict(os.environ, GKEY_HOME=str(ROOT / ".test-home"), GKEY_NO_FZF="1"),
    )
    assert res.returncode == 0 and "pdf2zh" in res.stdout
    import shutil
    shutil.rmtree(ROOT / ".test-home", ignore_errors=True)


def test_parse_command_line_drops_gkey_prefix():
    assert menu.parse_command_line("gkey pdf2zh 'my paper.pdf' -t 8") == ["pdf2zh", "my paper.pdf", "-t", "8"]
    assert menu.parse_command_line("   ") is None
    assert menu.parse_command_line("gkey") is None
