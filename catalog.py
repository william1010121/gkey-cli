#!/usr/bin/env python3
"""Curated Best-in-Class OpenAI CLI Tools Catalog, Auto-Installer & Smart Adapter for `gkey`."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field


def ensure_extended_path() -> None:
    """Ensures ~/.local/bin, ~/.npm-global/bin, ~/go/bin, ~/.cargo/bin, /opt/homebrew/bin are in PATH."""
    extra_dirs = [
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/.npm-global/bin"),
        os.path.expanduser("~/go/bin"),
        os.path.expanduser("~/.cargo/bin"),
        "/opt/homebrew/bin",
    ]
    current_path = os.environ.get("PATH", "")
    parts = current_path.split(os.pathsep) if current_path else []
    for d in reversed(extra_dirs):
        if os.path.isdir(d) and d not in parts:
            parts.insert(0, d)
    os.environ["PATH"] = os.pathsep.join(parts)


@dataclass
class ToolSpec:
    id: str
    name: str
    category: str
    stars: str
    binary: str
    aliases: list[str]
    repo: str
    summary: str
    why_useful: str
    install_cmd: str
    usage_examples: list[str]
    default_prompt_hint: str
    auto_adapt_note: str


TOOLS: list[ToolSpec] = [
    ToolSpec(
        id="pdf2zh",
        name="PDFMathTranslate (pdf2zh)",
        category="1. PDF / 論文翻譯",
        stars="30k+ ⭐",
        binary="pdf2zh",
        aliases=["pdfmathtranslate"],
        repo="https://github.com/Byaidu/PDFMathTranslate",
        summary="保留 LaTeX 數學公式、雙欄排版與圖表的論文雙語 PDF 翻譯神器",
        why_useful="看 arXiv 論文唯一不會把 LaTeX 數學公式、雙欄排版、圖表位置弄爛的翻譯工具，跑完直接生成「純譯文」與「中英雙語對照」兩份 PDF。",
        install_cmd="uv tool install pdf2zh",
        usage_examples=[
            "gkey pdf2zh paper.pdf",
            "gkey pdf2zh paper.pdf -p 1-5          # 只翻第 1~5 頁",
            "gkey -m gemini-4-argon-medium pdf2zh paper.pdf",
        ],
        default_prompt_hint="請輸入 PDF 檔案路徑 (例如 paper.pdf)",
        auto_adapt_note="自動補上 `-s openai`（無須手動輸入 -s openai！）",
    ),
    ToolSpec(
        id="babeldoc",
        name="BabelDOC",
        category="1. PDF / 論文翻譯",
        stars="新一代引擎 ⭐",
        binary="babeldoc",
        aliases=[],
        repo="https://github.com/funstory-ai/BabelDOC",
        summary="PDFMathTranslate 團隊新一代高精度 PDF 雙語排版翻譯引擎",
        why_useful="PDFMathTranslate 的次世代底層引擎，針對複雜跨欄段落與學術論文版面重構更精準。",
        install_cmd="uv tool install BabelDOC",
        usage_examples=[
            "gkey babeldoc paper.pdf",
            "gkey babeldoc --files paper.pdf --lang-out zh-TW",
        ],
        default_prompt_hint="請輸入 PDF 檔案路徑 (例如 paper.pdf)",
        auto_adapt_note="自動將位置參數轉為 `--files` 並注入 `--openai --openai-model --openai-base-url --openai-api-key`",
    ),
    ToolSpec(
        id="markitdown",
        name="MarkItDown (Microsoft)",
        category="1. PDF / 文件轉 MD",
        stars="190k+ ⭐",
        binary="markitdown",
        aliases=[],
        repo="https://github.com/microsoft/markitdown",
        summary="微軟官方維護：把 PDF、PPTX、DOCX、XLSX、圖片一次轉成乾淨 Markdown",
        why_useful="幾乎是目前把各種雜亂辦公室文件（PDF、PPTX、DOCX、XLSX、HTML、ZIP）一次轉成乾淨 Markdown 的業界標準。",
        install_cmd='uv tool install "markitdown[all]"',
        usage_examples=[
            "gkey markitdown input.pdf -o output.md",
            "gkey markitdown slides.pptx -o slides.md",
            "gkey markitdown report.docx",
        ],
        default_prompt_hint="請輸入文件路徑與輸出 (例如 input.pdf -o output.md)",
        auto_adapt_note="自動注入 `OPENAI_API_KEY` 與 `OPENAI_BASE_URL`",
    ),
    ToolSpec(
        id="marker_single",
        name="Marker",
        category="1. PDF / 文件轉 MD",
        stars="25k+ ⭐",
        binary="marker_single",
        aliases=["marker"],
        repo="https://github.com/VikParuchuri/marker",
        summary="掃描檔、跨頁複雜表格、滿滿數學公式 PDF 轉 Markdown 最強工具",
        why_useful="結合本地深度排版模型 + 外部 OpenAI LLM 校正，處理跨頁表格與複雜 LaTeX 數學公式準確率最高。",
        install_cmd="uv tool install marker-pdf",
        usage_examples=[
            "gkey marker_single paper.pdf",
            "gkey marker_single paper.pdf --output_dir ./md_out",
        ],
        default_prompt_hint="請輸入 PDF 檔案路徑 (例如 paper.pdf)",
        auto_adapt_note="自動注入 `--use_llm --llm_service marker.services.openai.OpenAIService --openai_api_key --openai_base_url --openai_model`",
    ),
    ToolSpec(
        id="gpt-subtrans",
        name="llm-subtrans (gpt-subtrans)",
        category="2. 字幕 / 電子書翻譯",
        stars="專用字幕 CLI ⭐",
        binary="gpt-subtrans",
        aliases=["llm-subtrans"],
        repo="https://github.com/machinewrapped/llm-subtrans",
        summary="純 CLI 將 .srt / .ass 字幕檔結合上下文翻成通順繁中且絕不弄亂時間軸",
        why_useful="已經有 .srt 或 .ass 字幕檔時，用批次前後文滑動視窗翻成通順的繁體中文，且 100% 保持時間軸對齊。",
        install_cmd='uv tool install --with openai "git+https://github.com/machinewrapped/llm-subtrans.git"',
        usage_examples=[
            "gkey gpt-subtrans movie.srt -o movie.zh.srt",
            "gkey gpt-subtrans movie.srt -l 'Traditional Chinese' -o movie.zh.srt",
        ],
        default_prompt_hint="請輸入字幕檔路徑 (例如 input.srt -o output.srt)",
        auto_adapt_note="自動注入 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`-l Traditional Chinese`",
    ),
    ToolSpec(
        id="bbook_maker",
        name="bilingual_book_maker",
        category="2. 字幕 / 電子書翻譯",
        stars="14k+ ⭐",
        binary="bbook_maker",
        aliases=["bilingual_book_maker"],
        repo="https://github.com/yihong0618/bilingual_book_maker",
        summary="丟一本外文 .epub 進去，直接吐出保留排版的「中英雙語對照 .epub」電子書",
        why_useful="保留原本 EPUB 章節結構與插圖，生成段落對照雙語電子書，支援 `--test` 先試翻前幾段。",
        install_cmd="uv tool install bilingual_book_maker",
        usage_examples=[
            "gkey bbook_maker book.epub",
            "gkey bbook_maker book.epub --test              # 只試翻前 10 段確認效果",
            "gkey bbook_maker --book_name book.epub --language 'Traditional Chinese'",
        ],
        default_prompt_hint="請輸入電子書路徑 (例如 book.epub --test)",
        auto_adapt_note="自動將 `book.epub` 轉為 `--book_name book.epub` 並補上 `--openai_key`、`--api_base`、`--language Traditional Chinese`",
    ),
    ToolSpec(
        id="epub_to_audiobook",
        name="epub_to_audiobook",
        category="2. 字幕 / 電子書翻譯",
        stars="有聲書神器 ⭐",
        binary="epub_to_audiobook",
        aliases=[],
        repo="https://github.com/p0n1/epub_to_audiobook",
        summary="把 .epub 電子書按章節切開，轉成帶完整章節書籤的有聲書",
        why_useful="自動解析 EPUB 章節並對接 `gkey` 內建的 `/v1/audio/speech` 本地語音合成端點生成有聲書。",
        install_cmd='uv tool install "git+https://github.com/p0n1/epub_to_audiobook.git"',
        usage_examples=[
            "gkey epub_to_audiobook book.epub ./audiobook_out",
        ],
        default_prompt_hint="請輸入 <輸入.epub> <輸出資料夾> (例如 book.epub ./out)",
        auto_adapt_note="自動補上 `--tts openai` 並由 `gkey` 內建 `/v1/audio/speech` 提供語音合成",
    ),
    ToolSpec(
        id="videocaptioner",
        name="VideoCaptioner (卡卡字幕助手)",
        category="2. 字幕 / 電子書翻譯",
        stars="熱門影音字幕 ⭐",
        binary="videocaptioner",
        aliases=["videolingo"],
        repo="https://github.com/WEIFENG2333/VideoCaptioner",
        summary="解決 Whisper 碎字幕問題，用 LLM 自動智慧斷句並生成雙語字幕",
        why_useful="自動將語音辨識後的破碎字幕智慧斷句成符合人類閱讀習慣的單行雙語字幕。",
        install_cmd="mkdir -p ~/.local/share/gkey && (test -d ~/.local/share/gkey/VideoCaptioner || git clone --depth 1 https://github.com/WEIFENG2333/VideoCaptioner.git ~/.local/share/gkey/VideoCaptioner)",
        usage_examples=[
            "gkey videocaptioner",
        ],
        default_prompt_hint="直接按 Enter 啟動 (已注入 OPENAI_BASE_URL 與 OPENAI_API_KEY)",
        auto_adapt_note="自動設定 `OPENAI_BASE_URL` 與 `OPENAI_API_KEY` 環境變數",
    ),
    ToolSpec(
        id="fabric",
        name="Fabric",
        category="3. UNIX 管線 / 批次處理",
        stars="30k+ ⭐",
        binary="fabric",
        aliases=["fabric-ai"],
        repo="https://github.com/danielmiessler/fabric",
        summary="內建數百種高頻 Prompt Pattern（論文提煉、會議記錄、YouTube 摘要）的 UNIX 管線神器",
        why_useful="Go 重寫版極快！一行 `cat paper.md | gkey fabric -p extract_wisdom` 或分析 Log 跑完即結束，完全不囉嗦。",
        install_cmd="go install github.com/danielmiessler/fabric/cmd/fabric@latest",
        usage_examples=[
            "cat article.md | gkey fabric -p summarize",
            "cat meeting.txt | gkey fabric -p extract_wisdom",
            "gkey fabric -L                                  # 列出所有內建 Pattern",
        ],
        default_prompt_hint="請輸入 fabric 參數 (例如 -L 列出所有 Pattern，或 -p summarize)",
        auto_adapt_note="自動建立 `~/.config/fabric/.env` 並注入 OpenAI Compatible 設定，免跑 `fabric --setup`",
    ),
    ToolSpec(
        id="mods",
        name="Mods (Charmbracelet)",
        category="3. UNIX 管線 / 批次處理",
        stars="Charm 出品 ⭐",
        binary="mods",
        aliases=[],
        repo="https://github.com/charmbracelet/mods",
        summary="極簡純粹的 UNIX 管線 LLM 工具，最適合洗 git diff、kubectl logs 或 CSV/JSON",
        why_useful="當你在寫 Shell Script 需要把 `git diff`、`error.log` 丟進去洗成乾淨 Markdown 表格時，穩定度最高的工業級 CLI。",
        install_cmd="brew install charmbracelet/tap/mods || go install github.com/charmbracelet/mods@latest",
        usage_examples=[
            'git diff | gkey mods "總結這次變更並檢查有無潛在 Bug"',
            'cat error.log | gkey mods "找出根因並列出修復指令"',
            'gkey mods "用表格比較 Nix 與 Homebrew 的差異"',
        ],
        default_prompt_hint='請輸入提示詞 (例如 "用表格列出 macOS 常用網路除錯指令")',
        auto_adapt_note="自動配置 `mods.yml` 的 `base-url` 並注入 `MODS_API=openai` 與 `MODS_MODEL`",
    ),
    ToolSpec(
        id="ai-renamer",
        name="ai-renamer",
        category="3. UNIX 管線 / 批次處理",
        stars="2.1k+ ⭐",
        binary="ai-renamer",
        aliases=[],
        repo="https://github.com/ozgrozer/ai-renamer",
        summary="自動讀取圖片與 PDF 內容，將亂碼截圖/掃描檔批次改名成有意義的檔名",
        why_useful="專門對付桌面和 Downloads 資料夾裡一堆 `IMG_0293.png`、`scan_01.pdf`，自動看內容改名成可讀檔名。",
        install_cmd="npm install -g ai-renamer",
        usage_examples=[
            "gkey ai-renamer ./ folder_to_rename",
            "gkey ai-renamer ~/Desktop --language='Traditional Chinese'",
        ],
        default_prompt_hint="請輸入要批次改名的資料夾路徑 (例如 ./screenshots)",
        auto_adapt_note="自動注入 `--provider=openai --api-key=... --base-url=... --model=gpt-4o-mini`",
    ),
    ToolSpec(
        id="oco",
        name="OpenCommit (oco)",
        category="4. Git / 專案多語系",
        stars="Node 生態最火 ⭐",
        binary="oco",
        aliases=["opencommit"],
        repo="https://github.com/di-sukharev/opencommit",
        summary="根據 git staged diff 在 1~2 秒內自動生成專業 Commit Message",
        why_useful="在 Git 專案中 `git add` 後直接跑 `gkey oco`，自動讀取 staged diff 生成符合規範的 commit 訊息。",
        install_cmd="npm install -g opencommit",
        usage_examples=[
            "gkey oco",
            "gkey oco --fgm                                  # 使用完整 GitMoji 規範",
        ],
        default_prompt_hint="直接按 Enter 在當前 Git Repo 執行 oco",
        auto_adapt_note="自動注入 `OCO_AI_PROVIDER=openai`、`OCO_API_URL`、`OCO_API_KEY`、`OCO_MODEL`",
    ),
    ToolSpec(
        id="gptcommit",
        name="gptcommit (Rust)",
        category="4. Git / 專案多語系",
        stars="Rust 極速版 ⭐",
        binary="gptcommit",
        aliases=[],
        repo="https://github.com/zurawiki/gptcommit",
        summary="Rust 編寫的極速 Git Commit Message 產生器与 git hook 工具",
        why_useful="Rust 原生執行檔極快、支援併發處理大型 diff，可掛載為 `prepare-commit-msg` hook。",
        install_cmd="brew install zurawiki/brews/gptcommit || cargo install --locked gptcommit",
        usage_examples=[
            "gkey gptcommit",
            "gkey gptcommit install                          # 在當前 repo 安裝 git hook",
        ],
        default_prompt_hint="直接按 Enter 執行 gptcommit",
        auto_adapt_note="自動注入 `GPTCOMMIT__OPENAI__API_BASE`、`GPTCOMMIT__OPENAI__API_KEY`、`GPTCOMMIT__OPENAI__MODEL`",
    ),
    ToolSpec(
        id="lobe-i18n",
        name="lobe-i18n (LobeHub)",
        category="4. Git / 專案多語系",
        stars="i18n 翻譯神器 ⭐",
        binary="lobe-i18n",
        aliases=[],
        repo="https://github.com/lobehub/lobe-cli-toolbox",
        summary="自動將專案語系檔 (JSON) 或 Markdown 批次翻譯成十幾種語言",
        why_useful="維護開源專案或 App 時，跑一行 `gkey lobe-i18n` 或 `gkey lobe-i18n md` 即可利用滑動視窗自動同步多語系檔案。",
        install_cmd="npm install -g @lobehub/i18n-cli",
        usage_examples=[
            "gkey lobe-i18n",
            "gkey lobe-i18n md                               # 批次翻譯 Markdown 文件",
        ],
        default_prompt_hint="直接按 Enter 執行 lobe-i18n (或輸入 md 翻譯 Markdown)",
        auto_adapt_note="自動注入 `OPENAI_PROXY_URL`（lobe-i18n 專用代理變數）與 `OPENAI_API_KEY`",
    ),
]

TOOL_BY_ID: dict[str, ToolSpec] = {}
for _t in TOOLS:
    TOOL_BY_ID[_t.id] = _t
    TOOL_BY_ID[_t.binary] = _t
    for _alias in _t.aliases:
        TOOL_BY_ID[_alias] = _t


def is_tool_installed(spec: ToolSpec) -> str | None:
    ensure_extended_path()
    if spec.id == "videocaptioner":
        vc_dir = os.path.expanduser("~/.local/share/gkey/VideoCaptioner")
        if os.path.isdir(vc_dir):
            return vc_dir
    path = shutil.which(spec.binary)
    if path:
        return path
    for alias in spec.aliases:
        path = shutil.which(alias)
        if path:
            return path
    return None


def install_tool(spec: ToolSpec) -> bool:
    ensure_extended_path()
    installed_path = is_tool_installed(spec)
    if installed_path:
        return True

    sys.stderr.write(
        f"\n\033[1;36m📦 [gkey] 偵測到尚未安裝 {spec.name} ({spec.binary})，正在自動為您安裝...\033[0m\n"
    )
    sys.stderr.write(f"\033[90m   執行: {spec.install_cmd}\033[0m\n\n")
    sys.stderr.flush()

    res = subprocess.run(spec.install_cmd, shell=True)
    if res.returncode != 0:
        sys.stderr.write(
            f"\n\033[1;31m❌ [gkey] 安裝 {spec.name} 失敗 (exit code {res.returncode})\033[0m\n"
        )
        return False

    ensure_extended_path()
    sys.stderr.write(f"\n\033[1;32m✅ [gkey] {spec.name} 安裝完成！\033[0m\n\n")
    return True


def _ensure_fabric_config(base_url: str, api_key: str, model: str) -> None:
    fabric_dir = pathlib.Path.home() / ".config" / "fabric"
    fabric_dir.mkdir(parents=True, exist_ok=True)
    env_file = fabric_dir / ".env"
    if not env_file.exists():
        env_file.write_text(
            f"DEFAULT_VENDOR=OpenAI\n"
            f"DEFAULT_MODEL={model}\n"
            f"OPENAI_API_KEY={api_key}\n"
            f"OPENAI_API_BASE_URL={base_url}\n",
            encoding="utf-8",
        )


def _ensure_mods_config(base_url: str, model: str) -> None:
    """Ensures Charmbracelet `mods` has a config file whose openai base-url points to gkey."""
    mods_dir = pathlib.Path("/tmp/gkey-mods-config")
    mods_dir.mkdir(parents=True, exist_ok=True)
    mods_yml = mods_dir / "mods.yml"
    mods_yml.write_text(
        f"default-api: openai\n"
        f"default-model: {model}\n"
        f"apis:\n"
        f"  openai:\n"
        f"    base-url: {base_url}\n"
        f"    api-key-env: OPENAI_API_KEY\n"
        f"    models:\n"
        f"      {model}:\n"
        f"        aliases: [\"default\", \"gpt-4o-mini\", \"gpt-4o\"]\n"
        f"        max-input-chars: 392000\n"
        f"      gpt-4o-mini:\n"
        f"        max-input-chars: 392000\n",
        encoding="utf-8",
    )


def adapt_and_exec(port: str, model: str, argv: list[str]) -> None:
    """Adapts CLI arguments and environment variables for the target tool, auto-installing if needed."""
    ensure_extended_path()
    if not argv:
        sys.exit(1)

    cmd_name = argv[0]
    args = list(argv[1:])

    base_url = f"http://127.0.0.1:{port}/v1"
    server_root = f"http://127.0.0.1:{port}"
    api_key = os.environ.get("OPENAI_API_KEY", "gkey-local-placeholder")

    # Universal OpenAI-compatible environment variables
    os.environ["OPENAI_BASE_URL"] = base_url
    os.environ["OPENAI_API_BASE"] = base_url
    os.environ["OPENAI_API_BASE_URL"] = base_url
    os.environ["OPENAI_PROXY_URL"] = base_url
    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_MODEL"] = model

    spec = TOOL_BY_ID.get(cmd_name)
    if spec is not None:
        if not install_tool(spec):
            sys.exit(1)
        # Always use the canonical binary name if an alias was used
        cmd_name = spec.binary

        # Help flags pass through untouched
        is_help = any(a in ("-h", "--help", "-v", "--version") for a in args)

        if not is_help:
            if spec.id == "pdf2zh":
                # Auto-inject `-s openai` if not provided by user
                if not any(a in ("-s", "--service") or a.startswith("-s=") for a in args):
                    args.extend(["-s", "openai"])

            elif spec.id == "babeldoc":
                # If user passed `gkey babeldoc paper.pdf`, rewrite positional pdf to `--files paper.pdf`
                if args and not any(a == "--files" for a in args):
                    new_args: list[str] = []
                    for a in args:
                        if not a.startswith("-") and a.lower().endswith(".pdf"):
                            new_args.extend(["--files", a])
                        else:
                            new_args.append(a)
                    args = new_args
                if "--openai" not in args:
                    args.extend(
                        [
                            "--openai",
                            "--openai-model",
                            model,
                            "--openai-base-url",
                            base_url,
                            "--openai-api-key",
                            api_key,
                        ]
                    )

            elif spec.id == "marker_single":
                if "--use_llm" not in args:
                    args.append("--use_llm")
                if "--llm_service" not in args:
                    args.extend(["--llm_service", "marker.services.openai.OpenAIService"])
                if "--openai_api_key" not in args:
                    args.extend(["--openai_api_key", api_key])
                if "--openai_base_url" not in args:
                    args.extend(["--openai_base_url", base_url])
                if "--openai_model" not in args:
                    args.extend(["--openai_model", model])

            elif spec.id == "gpt-subtrans":
                os.environ["OPENAI_MODEL"] = model
                if not any(a in ("-l", "--target_language") for a in args):
                    args.extend(["-l", "Traditional Chinese"])

            elif spec.id == "bbook_maker":
                # Allow `gkey bbook_maker book.epub` without typing `--book_name`
                if args and not args[0].startswith("-") and "--book_name" not in args:
                    args = ["--book_name", args[0]] + args[1:]
                if "--openai_key" not in args:
                    args.extend(["--openai_key", api_key])
                if "--api_base" not in args:
                    args.extend(["--api_base", base_url])
                if "--model" not in args:
                    args.extend(["--model", "gpt-4o-mini"])
                if "--language" not in args:
                    args.extend(["--language", "Traditional Chinese"])

            elif spec.id == "epub_to_audiobook":
                if "--tts" not in args:
                    args.extend(["--tts", "openai"])

            elif spec.id == "fabric":
                _ensure_fabric_config(base_url, api_key, model)
                os.environ["DEFAULT_VENDOR"] = "OpenAI"
                os.environ["DEFAULT_MODEL"] = model

            elif spec.id == "mods":
                _ensure_mods_config(base_url, model)
                os.environ["MODS_CONFIG"] = "/tmp/gkey-mods-config/mods.yml"

            elif spec.id == "ai-renamer":
                if not any(a.startswith("--provider") for a in args):
                    args.append("--provider=openai")
                if not any(a.startswith("--api-key") for a in args):
                    args.append(f"--api-key={api_key}")
                if not any(a.startswith("--base-url") for a in args):
                    args.append(f"--base-url={base_url}")
                if not any(a.startswith("--model") for a in args):
                    args.append(f"--model={model}")

            elif spec.id == "oco":
                os.environ["OCO_AI_PROVIDER"] = "openai"
                os.environ["OCO_API_KEY"] = api_key
                os.environ["OCO_API_URL"] = base_url
                os.environ["OCO_MODEL"] = "gpt-4o-mini"

            elif spec.id == "gptcommit":
                os.environ["GPTCOMMIT__OPENAI__API_KEY"] = api_key
                os.environ["GPTCOMMIT__OPENAI__API_BASE"] = base_url
                os.environ["GPTCOMMIT__OPENAI__MODEL"] = "gpt-4o-mini"

            elif spec.id == "lobe-i18n":
                os.environ["OPENAI_PROXY_URL"] = base_url
                os.environ["OPENAI_API_KEY"] = api_key

            elif spec.id == "videocaptioner":
                vc_dir = os.path.expanduser("~/.local/share/gkey/VideoCaptioner")
                print(f"✅ VideoCaptioner 目錄: {vc_dir}")
                print(f"   已自動注入 OPENAI_BASE_URL={base_url} 與 OPENAI_API_KEY={api_key}")
                sys.exit(0)

    os.execvp(cmd_name, [cmd_name] + args)


def print_fzf_list() -> None:
    """Outputs one line per tool for fzf."""
    for spec in TOOLS:
        installed = is_tool_installed(spec) is not None
        badge = "\033[32m✅已裝\033[0m" if installed else "\033[33m📦未裝\033[0m"
        print(
            f"{spec.id:<18}\t{badge}  \033[36m[{spec.category}]\033[0m \033[1m{spec.name:<28}\033[0m — {spec.summary}"
        )


def print_preview(tool_id: str) -> None:
    tool_id = tool_id.strip()
    spec = TOOL_BY_ID.get(tool_id)
    if not spec:
        print(f"找不到工具: {tool_id}")
        return

    installed_path = is_tool_installed(spec)
    status_str = (
        f"\033[1;32m✅ 已安裝 ({installed_path})\033[0m"
        if installed_path
        else "\033[1;33m📦 尚未安裝（按 Enter 選擇後將自動安裝！）\033[0m"
    )

    print(f"\033[1;36m━━━ {spec.name} ({spec.stars}) ━━━\033[0m")
    print(f"\033[90m分類:\033[0m {spec.category}")
    print(f"\033[90mGitHub:\033[0m {spec.repo}")
    print(f"\033[90m狀態:\033[0m {status_str}")
    print()
    print("\033[1;33m💡 為什麼推薦：\033[0m")
    print(f"  {spec.why_useful}")
    print()
    print("\033[1;32m🚀 gkey 零配置直接用法（不用記任何 API / Base URL 參數）：\033[0m")
    for ex in spec.usage_examples:
        print(f"  \033[1m{ex}\033[0m")
    print()
    print("\033[1;35m🪄 gkey 底層自動幫你適配了什麼：\033[0m")
    print(f"  {spec.auto_adapt_note}")
    print()
    print("\033[90m📦 自動安裝指令：\033[0m")
    print(f"  \033[90m{spec.install_cmd}\033[0m")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: catalog.py [list|preview <id>|install <id>|info <id>|adapt <port> <model> <cmd> ...]")
        sys.exit(1)

    action = sys.argv[1]
    if action == "list":
        print_fzf_list()
    elif action == "preview":
        print_preview(sys.argv[2] if len(sys.argv) > 2 else "")
    elif action == "install":
        spec = TOOL_BY_ID.get(sys.argv[2].strip())
        if not spec:
            sys.exit(1)
        if not install_tool(spec):
            sys.exit(1)
    elif action == "hint":
        spec = TOOL_BY_ID.get(sys.argv[2].strip())
        if spec:
            print(spec.default_prompt_hint)
    elif action == "adapt":
        port = sys.argv[2]
        model = sys.argv[3]
        adapt_and_exec(port, model, sys.argv[4:])


if __name__ == "__main__":
    main()
