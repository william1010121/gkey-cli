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


def test_pdf2zh_next_injects_openai_flags_once():
    args, env = adapt("pdf2zh-next", ["paper.pdf"])
    assert args == [
        "paper.pdf", "--openai", "--openai-model", MODEL, "--openai-base-url", f"http://127.0.0.1:{PORT}/v1",
        "--openai-api-key", LOCAL_API_KEY, "--lang-out", "zh-TW", "--qps", "16", "--pool-max-workers", "16",
    ]
    assert env["GKEY_PDF2ZH_LAYOUT"] == "zh-then-en"
    args, _ = adapt("pdf2zh-next", ["paper.pdf", "--deepseek"])
    assert "--openai" not in args and "--openai-base-url" not in args
    args, _ = adapt("pdf2zh-next", ["paper.pdf", "--openai", "--openai-model", "x"])
    assert args.count("--openai") == 1 and args.count("--openai-model") == 1 and args.count("--openai-base-url") == 1


def test_pdf2zh_alias_maps_to_next():
    assert catalog.TOOL_BY_ID["pdf2zh"] is catalog.TOOL_BY_ID["pdf2zh-next"]
    assert catalog.TOOL_BY_ID["pdf2zh_next"].binary == "pdf2zh_next"
    assert "pdf2zh" not in catalog.TOOL_BY_ID["pdf2zh-next"].aliases  # the old binary must not count as installed


def test_pdf2zh_next_concurrency_user_wins():
    args, _ = adapt("pdf2zh-next", ["paper.pdf", "--qps", "8"])
    assert args.count("--qps") == 1 and "--pool-max-workers" not in args
    args, _ = adapt("pdf2zh-next", ["paper.pdf", "--pool-max-workers=3"])
    assert "--qps" not in args and "--pool-max-workers=3" in args


def test_pdf2zh_next_target_language_default_and_override():
    args, _ = adapt("pdf2zh-next", ["paper.pdf"])
    assert args[args.index("--lang-out") + 1] == "zh-TW"
    for given in (["--lang-out", "ja"], ["--lang-out=ja"]):
        args, _ = adapt("pdf2zh-next", ["paper.pdf"] + given)
        assert "zh-TW" not in args
        assert sum(a.startswith("--lang-out") for a in args) == 1


def test_pdf2zh_next_pages_only_translated():
    args, _ = adapt("pdf2zh-next", ["paper.pdf", "--pages", "1-5"])
    assert "--only-include-translated-page" in args
    args, _ = adapt("pdf2zh-next", ["paper.pdf"])
    assert "--only-include-translated-page" not in args


def test_pdf2zh_layout_flag_is_consumed():
    args, env = adapt("pdf2zh-next", ["paper.pdf", "--layout", "dual"])
    assert "--layout" not in args and "dual" not in args and env["GKEY_PDF2ZH_LAYOUT"] == "dual"
    args, env = adapt("pdf2zh-next", ["--layout=zh-then-en", "paper.pdf"])
    assert args[0] == "paper.pdf" and env["GKEY_PDF2ZH_LAYOUT"] == "zh-then-en"
    with pytest.raises(SystemExit):
        adapt("pdf2zh-next", ["paper.pdf", "--layout", "weird"])


def test_parse_pdf2zh_args():
    info = catalog.parse_pdf2zh_args(
        ["a b.pdf", "--pages", "1-3", "--qps", "8", "--output", "out", "--lang-out", "zh-TW", "--openai", "--no-dual", "c.pdf"]
    )
    assert info["files"] == ["a b.pdf", "c.pdf"]
    assert (info["output"], info["pages"], info["lang_out"], info["lang_in"]) == ("out", "1-3", "zh-TW", "en")
    assert catalog.parse_pdf2zh_args(["--output=x", "--pages=2", "p.pdf"])["output"] == "x"
    assert catalog.parse_pdf2zh_args(["--pages", "p.pdf"])["files"] == ["p.pdf"]  # missing value: file kept


def test_pdf2zh_output_paths():
    assert catalog.pdf2zh_output_paths("o", "paper", "zh-TW") == ("o/paper.zh-TW.mono.pdf", "o/paper.zh-TW.dual.pdf")


def test_babeldoc_positional_pdf_rewritten():
    args, _ = adapt("babeldoc", ["paper.pdf"])
    assert args[:2] == ["--files", "paper.pdf"]
    assert "--openai-base-url" in args and f"http://127.0.0.1:{PORT}/v1" in args
    assert LOCAL_API_KEY in args


def test_babeldoc_default_lang_out_zh_tw_and_user_wins():
    args, _ = adapt("babeldoc", ["paper.pdf"])
    assert args[args.index("--lang-out") + 1] == "zh-TW" and args.count("--lang-out") == 1
    for user in (["--lang-out", "ja"], ["--lang-out=ja"], ["-lo", "ja"], ["-lo=ja"], ["-loja"]):
        args, _ = adapt("babeldoc", ["paper.pdf"] + user)
        assert not ("zh-TW" in args), user
    args, _ = adapt("babeldoc", ["paper.pdf", "-li", "en"])  # -li must not be mistaken for -lo
    assert "zh-TW" in args


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
    assert args[args.index("-m") + 1] == "gpt-4o-mini"  # chat-completions model name, not the gkey model
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
    catalog.adapt_and_exec(PORT, MODEL, ["pdf2zh-next", "--help"])
    assert seen["argv"] == ["pdf2zh_next", "--help"]


def test_legacy_alias_prints_notice(monkeypatch, capsys):
    monkeypatch.setattr(catalog.os, "execvp", lambda cmd, argv: None)
    monkeypatch.setattr(catalog, "install_tool", lambda spec: True)
    catalog.adapt_and_exec(PORT, MODEL, ["pdf2zh", "--help"])
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "pdf2zh-next" in err


def test_poolsize_cli():
    out = subprocess.run(
        [sys.executable, str(ROOT / "catalog.py"), "poolsize", "4", "-", "pdf2zh", "p.pdf", "--qps", "8"],
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
    monkeypatch.setattr(catalog, "_ensure_fabric_patterns", lambda *a, **k: None)  # never run a real `fabric -U`


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


def test_gkey_help_has_no_shell_expansion_in_heredoc():
    """The help text is an unquoted bash heredoc: a backtick or $( would run a command when printing it."""
    import pathlib
    text = pathlib.Path(__file__).resolve().parent.parent.joinpath("gkey").read_text()
    start = text.index("cat <<EOF")
    end = text.index("\nEOF", start)
    body = text[start:end]
    assert "`" not in body and "$(" not in body


# ---- tool-bug fixes found by the benchmark -------------------------------------------------------

def test_gpt_subtrans_uses_chat_completions_model_name():
    args, _ = adapt("gpt-subtrans", ["a.srt"])
    model = args[args.index("-m") + 1]
    assert model == "gpt-4o-mini" and model != MODEL
    # llm-subtrans treats everything not starting with gpt-3 / gpt-4 / gpt-5-chat as a /v1/responses model
    assert model.startswith("gpt-4")


def test_gpt_subtrans_is_a_source_install(monkeypatch, tmp_path):
    monkeypatch.setenv("GKEY_HOME", str(tmp_path))
    spec = catalog.TOOL_BY_ID["gpt-subtrans"]
    assert spec.source_install and "console" not in spec.install_cmd
    assert catalog.is_tool_installed(spec) is None
    paths = catalog.source_paths(spec)
    paths["python"].parent.mkdir(parents=True)
    paths["python"].write_text("")
    paths["script"].parent.mkdir(parents=True)
    paths["script"].write_text("")
    assert catalog.is_tool_installed(spec) == str(paths["script"])


def test_install_source_tool_runs_clone_venv_pip(monkeypatch, tmp_path):
    monkeypatch.setenv("GKEY_HOME", str(tmp_path))
    spec = catalog.TOOL_BY_ID["gpt-subtrans"]
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        paths = catalog.source_paths(spec)
        if cmd[0] == "git":
            paths["script"].parent.mkdir(parents=True)
            paths["script"].write_text("")
        if cmd[:2] == ["uv", "venv"]:
            paths["python"].parent.mkdir(parents=True)
            paths["python"].write_text("")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(catalog.subprocess, "run", fake_run)
    assert catalog.install_tool(spec) is True
    assert [c[0] for c in calls] == ["git", "uv", "uv"]
    assert calls[0][:3] == ["git", "clone", "--depth"]
    assert "openai" in calls[2] and "-e" in calls[2]
    calls.clear()
    assert catalog.install_tool(spec) is True and calls == []  # already installed


def test_source_launch_runs_from_checkout_with_absolute_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("GKEY_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    (work / "in.srt").write_text("x")
    monkeypatch.chdir(work)
    spec = catalog.TOOL_BY_ID["gpt-subtrans"]
    argv, cwd = catalog.source_launch(spec, ["in.srt", "-o", "out.srt", "-l", "Japanese", "--output=o2.srt"])
    paths = catalog.source_paths(spec)
    assert cwd == paths["repo"]
    assert argv[:2] == [str(paths["python"]), str(paths["script"])]
    assert argv[2:] == [str(work / "in.srt"), "-o", str(work / "out.srt"), "-l", "Japanese", f"--output={work / 'o2.srt'}"]


LOBE_RC = '{"entry": "locales/en-US.json", "entryLocale": "en-US", "output": "locales", "outputLocales": ["zh-TW"]}'


def test_lobe_i18n_config_saves_immediately(monkeypatch, tmp_path):
    monkeypatch.setenv("GKEY_HOME", str(tmp_path / "gk"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".i18nrc").write_text(LOBE_RC)
    monkeypatch.chdir(proj)
    import json
    args, _ = adapt("lobe-i18n", [])
    cfg = json.loads(pathlib.Path(args[1]).read_text())
    assert cfg["saveImmediately"] is True and cfg["modelName"] == "gpt-4o-mini"


def test_lobe_i18n_fast_values_reach_the_config(monkeypatch, tmp_path, capsys):
    """--fast writes GKEY_LOBE_* only into the child env; the merged config must still carry them."""
    import json
    monkeypatch.setenv("GKEY_HOME", str(tmp_path / "gk"))
    monkeypatch.setenv("GKEY_FAST", "1")
    monkeypatch.delenv("GKEY_LOBE_CONCURRENCY", raising=False)
    monkeypatch.delenv("GKEY_LOBE_SPLIT_TOKEN", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".i18nrc").write_text(LOBE_RC)
    monkeypatch.chdir(proj)
    monkeypatch.setattr(catalog, "install_tool", lambda spec: True)
    seen = {}
    monkeypatch.setattr(catalog.os, "execvp", lambda f, a: seen.update(argv=a))
    catalog.adapt_and_exec(PORT, MODEL, ["lobe-i18n"])
    cfg = json.loads(pathlib.Path(seen["argv"][2]).read_text())
    assert cfg["concurrency"] == 8 and cfg["splitToken"] == 3000
    # without --fast nothing is injected
    monkeypatch.setenv("GKEY_FAST", "")
    for k in ("GKEY_LOBE_CONCURRENCY", "GKEY_LOBE_SPLIT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    args, _ = adapt("lobe-i18n", [])
    cfg = json.loads(pathlib.Path(args[1]).read_text())
    assert "concurrency" not in cfg and "splitToken" not in cfg


def test_fabric_downloads_patterns_once(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GKEY_HOME", str(tmp_path / "gk"))
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        assert kw.get("stdin") is subprocess.DEVNULL and kw.get("stdout") is sys.stderr  # never eat piped input / pollute stdout
        d = tmp_path / "home" / ".config" / "fabric" / "patterns" / "summarize"
        d.mkdir(parents=True)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(catalog.subprocess, "run", fake_run)
    adapt("fabric", ["-p", "summarize"])
    assert calls == [["fabric", "-U"]]
    adapt("fabric", ["-p", "summarize"])
    assert calls == [["fabric", "-U"]]  # patterns exist now: not repeated


def test_fabric_patterns_skipped_for_help_and_explicit_update(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GKEY_HOME", str(tmp_path / "gk"))
    monkeypatch.setattr(catalog.subprocess, "run", lambda *a, **k: pytest.fail("must not run fabric -U"))
    for extra in (["-U"], ["--setup"], ["-h"]):
        adapt("fabric", extra)


def test_fabric_patterns_failure_only_warns(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GKEY_HOME", str(tmp_path / "gk"))
    monkeypatch.setattr(catalog.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1))
    adapt("fabric", ["-L"])
    assert "fabric -U` failed" in capsys.readouterr().err


def test_no_model_tools_skip_the_proxy():
    assert catalog.TOOL_BY_ID["markitdown"].no_model is True
    assert [t.id for t in catalog.TOOLS if t.no_model] == ["markitdown"]
    run = lambda tool: subprocess.run([sys.executable, str(ROOT / "catalog.py"), "needs-proxy", tool]).returncode
    assert run("markitdown") == 1
    assert run("fabric") == 0 and run("unknown-tool") == 0


def test_gkey_launcher_does_not_start_proxy_for_markitdown(tmp_path):
    """Runs the real `gkey` script with a fake python for catalog.py: no server.py start for no_model tools."""
    log = tmp_path / "calls.log"
    fake = tmp_path / "fakepy"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> {log}\n'
        'case "$2" in needs-proxy) [[ "$3" == markitdown ]] && exit 1 || exit 0;; esac\n'
        'exit 0\n'
    )
    fake.chmod(0o755)
    import os
    env = dict(os.environ, GKEY_PYTHON=str(fake), GKEY_HOME=str(tmp_path / "home"), GKEY_PORT="18999")
    r = subprocess.run(["bash", str(ROOT / "gkey"), "markitdown", "x.pdf"], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert "server.py" not in calls and " adapt 18999" in calls
