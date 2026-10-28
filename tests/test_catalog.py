import pathlib
import re
import subprocess
import sys

import pytest

import catalog
from gkey_common import LOCAL_API_KEY

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT, MODEL = "18432", "gemini-3.8-flash-low"


def adapt(tool, args, monkeypatch=None, tmp_path=None):
    if monkeypatch is not None and tmp_path is not None:
        monkeypatch.setenv("GKEY_HOME", str(tmp_path))
    env = {}
    return catalog.adapt_args(catalog.TOOL_BY_ID[tool], args, PORT, MODEL, env), env


def test_pdf2zh_gets_service_once():
    args, env = adapt("pdf2zh", ["paper.pdf", "-t", "8"])
    assert args == ["paper.pdf", "-t", "8", "-s", "openai", "-lo", "zh-TW"]
    assert env["GKEY_PDF2ZH_LAYOUT"] == "zh-then-en"
    args, _ = adapt("pdf2zh", ["paper.pdf", "-s", "google"])
    assert args.count("-s") == 1 and "openai" not in args


def test_pdf2zh_target_language_default_and_override():
    args, _ = adapt("pdf2zh", ["paper.pdf"])
    assert args[args.index("-lo") + 1] == "zh-TW"
    for given in (["-lo", "ja"], ["--lang-out", "ja"], ["--lang-out=ja"], ["-lo=ja"]):
        args, _ = adapt("pdf2zh", ["paper.pdf"] + given)
        assert "zh-TW" not in args
        assert sum(a.startswith(("-lo", "--lang-out")) for a in args) == 1


def test_pdf2zh_layout_flag_is_consumed():
    args, env = adapt("pdf2zh", ["paper.pdf", "--layout", "dual"])
    assert "--layout" not in args and "dual" not in args and env["GKEY_PDF2ZH_LAYOUT"] == "dual"
    args, env = adapt("pdf2zh", ["--layout=zh-then-en", "paper.pdf"])
    assert args[0] == "paper.pdf" and env["GKEY_PDF2ZH_LAYOUT"] == "zh-then-en"
    with pytest.raises(SystemExit):
        adapt("pdf2zh", ["paper.pdf", "--layout", "weird"])


def test_parse_pdf2zh_args():
    info = catalog.parse_pdf2zh_args(["a b.pdf", "-p", "1-3", "-t", "8", "-o", "out", "-lo", "zh-TW", "-s", "openai", "c.pdf"])
    assert info["files"] == ["a b.pdf", "c.pdf"]
    assert (info["output"], info["pages"], info["lang_out"], info["lang_in"]) == ("out", "1-3", "zh-TW", "en")
    assert catalog.parse_pdf2zh_args(["--output=x", "--pages=2", "p.pdf"])["output"] == "x"


def test_babeldoc_positional_pdf_rewritten():
    args, _ = adapt("babeldoc", ["paper.pdf"])
    assert args[:2] == ["--files", "paper.pdf"]
    assert "--openai-base-url" in args and f"http://127.0.0.1:{PORT}/v1" in args
    assert LOCAL_API_KEY in args


def test_bbook_maker_book_name_and_language(monkeypatch):
    monkeypatch.setenv("GKEY_TARGET_LANG", "Japanese")
    args, _ = adapt("bbook_maker", ["book.epub", "--test"])
    assert args[:2] == ["--book_name", "book.epub"]
    assert args[args.index("--language") + 1] == "Japanese"


def test_user_flags_not_overridden():
    args, _ = adapt("marker_single", ["a.pdf", "--openai_model", "x"])
    assert args.count("--openai_model") == 1


def test_env_based_tools(monkeypatch, tmp_path):
    _, env = adapt("oco", [])
    assert env["OCO_API_KEY"] == LOCAL_API_KEY
    assert env["OCO_API_URL"] == f"http://127.0.0.1:{PORT}/v1"
    _, env = adapt("mods", [], monkeypatch, tmp_path)
    assert env["MODS_CONFIG"].startswith(str(tmp_path))


def test_real_openai_key_is_never_inherited(monkeypatch, capfd):
    real = "sk-proj-REALKEYREALKEYREALKEY1234"
    monkeypatch.setenv("OPENAI_API_KEY", real)
    seen = {}
    monkeypatch.setattr(catalog.os, "execvp", lambda cmd, argv: seen.update(argv=argv, key=catalog.os.environ["OPENAI_API_KEY"]))
    monkeypatch.setattr(catalog, "install_tool", lambda spec: True)
    catalog.adapt_and_exec(PORT, MODEL, ["marker_single", "a.pdf"])
    assert seen["key"] == LOCAL_API_KEY
    assert real not in " ".join(seen["argv"])


def test_videocaptioner_runs_real_cli_with_env_and_never_leaks_key(monkeypatch):
    real = "sk-proj-REALKEYREALKEYREALKEY1234"
    monkeypatch.setenv("OPENAI_API_KEY", real)
    seen = {}
    monkeypatch.setattr(
        catalog.os, "execvp",
        lambda cmd, argv: seen.update(cmd=cmd, argv=argv, env=dict(catalog.os.environ)),
    )
    monkeypatch.setattr(catalog, "install_tool", lambda spec: True)
    catalog.adapt_and_exec(PORT, MODEL, ["videocaptioner", "subtitle", "a.srt", "--thread-num", "8"])
    assert seen["cmd"] == "videocaptioner"
    assert seen["argv"][1:] == ["subtitle", "a.srt", "--thread-num", "8"]
    assert seen["env"]["OPENAI_API_KEY"] == LOCAL_API_KEY
    assert seen["env"]["OPENAI_BASE_URL"] == f"http://127.0.0.1:{PORT}/v1"
    assert seen["env"]["OPENAI_MODEL"] == MODEL
    assert real not in " ".join(seen["argv"])


def test_videocaptioner_install_installs_the_cli():
    cmd = catalog.TOOL_BY_ID["videocaptioner"].install_cmd
    assert "videocaptioner" in cmd and "git clone" not in cmd and "--with httpx" in cmd


def test_gpt_subtrans_points_at_proxy_and_respects_user_flags():
    args, _ = adapt("gpt-subtrans", ["a.srt", "-o", "b.srt"])
    assert args[args.index("-b") + 1] == f"http://127.0.0.1:{PORT}/v1"
    assert args[args.index("-m") + 1] == MODEL
    assert args[args.index("-l") + 1] == "Traditional Chinese"
    args, _ = adapt("gpt-subtrans", ["a.srt", "--target-language", "Japanese", "--apibase=http://x/v1", "-m", "m"])
    assert "-l" not in args and "-b" not in args and args.count("-m") == 1
    assert catalog.TOOL_BY_ID["gpt-subtrans"].batch_flag == "--maxbatchsize"


def test_bbook_maker_uses_current_flags_and_gkey_model():
    args, _ = adapt("bbook_maker", ["book.epub"])
    assert args[args.index("--key") + 1] == LOCAL_API_KEY
    assert args[args.index("--api_base") + 1] == f"http://127.0.0.1:{PORT}/v1"
    assert args[args.index("-m") + 1] == MODEL
    assert "--openai_key" not in args and "gpt-4o-mini" not in args
    args, _ = adapt("bbook_maker", ["book.epub", "--model", "x", "--openai_key", "k"])
    assert "-m" not in args and "--key" not in args


def test_babeldoc_install_pins_bitstring():
    assert 'bitstring<5' in catalog.TOOL_BY_ID["babeldoc"].install_cmd


def test_bbook_maker_install_uses_pypi_name():
    assert catalog.TOOL_BY_ID["bbook_maker"].install_cmd == "uv tool install bbook_maker"


def test_fast_profiles_declared():
    by = catalog.TOOL_BY_ID
    assert by["babeldoc"].fast["flags"] == ["--qps", "8", "--pool-max-workers", "24", "--no-auto-extract-glossary"]
    assert by["marker_single"].fast["flags"] == ["--max_concurrency", "8", "--timeout", "120"]
    assert by["bbook_maker"].fast["flags"] == ["--parallel-workers", "4"]
    assert by["epub_to_audiobook"].fast["flags"] == ["--worker_count", "4"]
    assert by["videocaptioner"].fast["subcommands"] == ["subtitle", "process"]
    assert by["markitdown"].fast is None


def test_help_flags_pass_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(catalog.os, "execvp", lambda cmd, argv: seen.update(argv=argv))
    monkeypatch.setattr(catalog, "install_tool", lambda spec: True)
    catalog.adapt_and_exec(PORT, MODEL, ["pdf2zh", "--help"])
    assert seen["argv"] == ["pdf2zh", "--help"]


def test_poolsize_cli():
    out = subprocess.run(
        [sys.executable, str(ROOT / "catalog.py"), "poolsize", "4", "-", "pdf2zh", "p.pdf", "-t", "8"],
        stdout=subprocess.PIPE, universal_newlines=True, check=True,
    ).stdout.strip()
    assert out == "8"


def test_no_cjk_or_personal_paths_in_sources():
    cjk = re.compile(r"[㐀-鿿　-〿＀-￯]")
    for name in ["gkey", "catalog.py", "server.py", "menu.py", "gkey_common.py", "bin/gkey.js", "README.md"]:
        p = ROOT / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        assert not cjk.search(text), f"CJK text left in {name}"
        assert "/Users/" not in text, f"hardcoded path in {name}"


def test_no_secret_shaped_strings_in_repo():
    pat = re.compile(r"sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}")
    for p in ROOT.rglob("*"):
        if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts and p.suffix in {".py", ".js", ".md", ".json", ""}:
            if "tests" in p.parts:
                continue
            assert not pat.search(p.read_text(encoding="utf-8", errors="ignore")), p
