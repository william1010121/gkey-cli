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
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "xdg")


def test_real_openai_key_is_never_inherited(monkeypatch, capfd):
    real = "sk-proj-REALKEYREALKEYREALKEY1234"
    monkeypatch.setenv("OPENAI_API_KEY", real)
    seen = {}
    monkeypatch.setattr(catalog.os, "execvp", lambda cmd, argv: seen.update(argv=argv, key=catalog.os.environ["OPENAI_API_KEY"]))
    monkeypatch.setattr(catalog, "install_tool", lambda spec: True)
    catalog.adapt_and_exec(PORT, MODEL, ["marker_single", "a.pdf"])
    assert seen["key"] == LOCAL_API_KEY
    assert real not in " ".join(seen["argv"])


def test_videocaptioner_output_never_prints_real_key(monkeypatch, capfd, tmp_path):
    real = "sk-proj-REALKEYREALKEYREALKEY1234"
    monkeypatch.setenv("OPENAI_API_KEY", real)
    monkeypatch.setenv("GKEY_HOME", str(tmp_path))
    monkeypatch.setattr(catalog, "install_tool", lambda spec: True)
    try:
        catalog.adapt_and_exec(PORT, MODEL, ["videocaptioner"])
    except SystemExit:
        pass
    out = capfd.readouterr()
    assert real not in out.out + out.err


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


# ---- CLI tools: fabric / mods / ai-renamer / oco / gptcommit / lobe-i18n --------------------------

def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GKEY_HOME", str(tmp_path / "gk"))
    (tmp_path / "home").mkdir(exist_ok=True)


def test_fabric_uses_litellm_vendor_not_responses_api(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    _, env = adapt("fabric", [])
    assert env["DEFAULT_VENDOR"] == "LiteLLM" and env["DEFAULT_MODEL"] == MODEL
    assert env["LITELLM_API_BASE_URL"] == f"http://127.0.0.1:{PORT}/v1"
    assert env["LITELLM_API_KEY"] == LOCAL_API_KEY
    dotenv = tmp_path / "home" / ".config" / "fabric" / ".env"
    assert "DEFAULT_VENDOR=LiteLLM" in dotenv.read_text()


def test_fabric_env_file_refresh_rules(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    d = tmp_path / "home" / ".config" / "fabric"
    d.mkdir(parents=True)
    legacy = "DEFAULT_VENDOR=OpenAI\nOPENAI_API_BASE_URL=http://127.0.0.1:1111/v1\n"
    (d / ".env").write_text(legacy)
    adapt("fabric", [])
    assert "LiteLLM" in (d / ".env").read_text()
    (d / ".env").write_text("DEFAULT_VENDOR=Anthropic\n")  # user-owned: untouched
    adapt("fabric", [])
    assert (d / ".env").read_text() == "DEFAULT_VENDOR=Anthropic\n"


def test_mods_yaml_has_no_duplicate_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("GKEY_HOME", str(tmp_path))
    for model in ("gpt-4o-mini", "gemini-3.8-flash-low"):
        env = {}
        catalog.adapt_args(catalog.TOOL_BY_ID["mods"], [], PORT, model, env)
        text = (tmp_path / "xdg" / "mods" / "mods.yml").read_text()
        keys = re.findall(r"^      ([\w.-]+):$", text, re.M)
        assert keys == [model]
        assert f"base-url: http://127.0.0.1:{PORT}/v1" in text
        assert (f'"{model}"' in text) is False


def test_mods_config_not_rewritten_when_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("GKEY_HOME", str(tmp_path))
    adapt("mods", [])
    f = tmp_path / "xdg" / "mods" / "mods.yml"
    before = f.stat().st_mtime_ns
    adapt("mods", [])
    assert f.stat().st_mtime_ns == before


def test_ai_renamer_base_url_has_no_v1_and_private_home(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    args, env = adapt("ai-renamer", ["/tmp/x"])
    assert f"--base-url=http://127.0.0.1:{PORT}" in args
    assert not any(a.endswith("/v1") for a in args)
    assert env["HOME"] == str(tmp_path / "gk" / "ai-renamer-home")
    args, _ = adapt("ai-renamer", ["--base-url=http://h:1", "x"])
    assert sum(a.startswith("--base-url") for a in args) == 1


def test_gptcommit_without_subcommand_shows_help(monkeypatch, capsys):
    args, env = adapt("gptcommit", [])
    assert args == ["--help"] and "subcommand" in capsys.readouterr().err
    args, _ = adapt("gptcommit", ["install"])
    assert args == ["install"]


def test_lobe_i18n_merged_config(monkeypatch, tmp_path):
    import json
    _home(monkeypatch, tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".i18nrc.json").write_text(json.dumps(
        {"entry": "locales/en.json", "entryLocale": "en", "output": "locales", "outputLocales": ["zh-TW"],
         "modelName": "gemini-3.8-flash"}))
    monkeypatch.chdir(proj)
    monkeypatch.setenv("GKEY_LOBE_CONCURRENCY", "8")
    monkeypatch.setenv("GKEY_LOBE_SPLIT_TOKEN", "3000")
    args, env = adapt("lobe-i18n", ["md"])
    assert args[0] == "-c" and args[2:] == ["md"]
    merged = json.loads(pathlib.Path(args[1]).read_text())
    assert merged["modelName"] == "gpt-4o-mini"  # supported model: no NaN splitToken
    assert (merged["concurrency"], merged["splitToken"]) == (8, 3000)
    assert merged["entry"] == "locales/en.json"
    assert env["OPENAI_PROXY_URL"] == f"http://127.0.0.1:{PORT}/v1"


def test_lobe_i18n_package_json_and_user_config_flag(monkeypatch, tmp_path):
    import json
    _home(monkeypatch, tmp_path)
    proj = tmp_path / "p2"
    (proj / "sub").mkdir(parents=True)
    (proj / "package.json").write_text(json.dumps({"name": "x", "i18n": {"entry": "a", "modelName": "zzz"}}))
    monkeypatch.chdir(proj / "sub")
    monkeypatch.delenv("GKEY_LOBE_CONCURRENCY", raising=False)
    monkeypatch.delenv("GKEY_LOBE_SPLIT_TOKEN", raising=False)
    args, _ = adapt("lobe-i18n", [])
    merged = json.loads(pathlib.Path(args[1]).read_text())
    assert merged["modelName"] == "gpt-4o-mini" and "concurrency" not in merged and "name" not in merged
    args, _ = adapt("lobe-i18n", ["-c", "mine.json"])
    assert args == ["-c", "mine.json"]


def test_lobe_i18n_without_json_config(monkeypatch, tmp_path, capsys):
    _home(monkeypatch, tmp_path)
    (tmp_path / "e").mkdir()
    (tmp_path / "e" / ".i18nrc.js").write_text("module.exports={}")
    monkeypatch.chdir(tmp_path / "e")
    args, _ = adapt("lobe-i18n", [])
    assert args == [] and "No JSON lobe-i18n config" in capsys.readouterr().err


def test_fast_profiles_declared_for_cli_tools():
    t = catalog.TOOL_BY_ID
    assert t["fabric"].fast["flags"][0] == "--maxTokens"
    assert t["mods"].fast["flags"] == ["--no-cache", "--max-retries", "2"]
    assert t["ai-renamer"].fast["flags"] == ["--frames", "2"]
    assert t["oco"].fast["env"]["OCO_TOKENS_MAX_INPUT"] == "32768"
    assert t["gptcommit"].fast["env"]["GPTCOMMIT__OPENAI__RETRIES"] == "1"
    assert t["lobe-i18n"].fast["env"]["GKEY_LOBE_CONCURRENCY"] == "8"
    assert all(t[i].fast["pool"] <= 16 for i in ("fabric", "mods", "ai-renamer", "oco", "gptcommit", "lobe-i18n"))
