#!/usr/bin/env python3
"""Tool catalog, auto-installer and argument/env adapter for `gkey`."""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gkey_speed  # noqa: E402
from gkey_common import (  # noqa: E402
    LOCAL_API_KEY,
    PDF2ZH_NEXT_FAST_QPS,
    compute_pool_size,
    detect_concurrency,
    gkey_home,
    mask_secret,
)


def ensure_extended_path() -> None:
    """Make sure common user-level bin dirs are on PATH (uv, npm -g, go, cargo, brew)."""
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


def _color(code: str, text: str, stream=None) -> str:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") or not hasattr(stream, "isatty") or not stream.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def target_language() -> str:
    return os.environ.get("GKEY_TARGET_LANG") or "Traditional Chinese"


@dataclass
class ToolSpec:
    id: str
    name: str
    category: str
    binary: str
    aliases: List[str]
    repo: str
    summary: str
    why_useful: str
    install_cmd: str
    usage_examples: List[str]
    default_prompt_hint: str
    auto_adapt_note: str
    # Speed contract (see gkey_speed.py); None = not tuned / unsupported. `fast` =
    # {"flags": [...], "env": {...}, "pool": N} injected (when the user did not pass them) by
    # the fast profile; optional extra keys: "subcommands" (apply only to these first-arg
    # subcommands) and "skip_if_any" (do not apply when any of these flags is present).
    # `batch_flag` names the tool's batch-size knob; `per_file` marks one-input-per-run tools.
    fast: Optional[dict] = None
    batch_flag: Optional[str] = None
    per_file: Optional[dict] = None
    # True for tools that never call a model: gkey skips starting the proxy for them.
    no_model: bool = False
    # Source-only tools (no console script): {"repo", "dir", "script", "pip": [...]}. gkey clones the repo and
    # builds a venv under GKEY_HOME/tools/<dir>, then runs `<venv python> <script>` from the checkout.
    source_install: Optional[dict] = None


# `uv tool install` refuses to replace the `pdf2zh` executable of the old PDFMathTranslate, which pdf2zh-next also ships,
# unless --force is given. bitstring 5.0 removed BitStream, which BabelDOC 0.6 imports.
PDF2ZH_NEXT_INSTALL_CMD = 'uv tool install --force --with "bitstring<5" pdf2zh-next'
# Old tool ids that now run another tool (printed as a one-line notice).
LEGACY_ALIASES = {"pdf2zh": "pdf2zh-next"}

TOOLS: list[ToolSpec] = [
    ToolSpec(
        id="pdf2zh-next",
        name="PDFMathTranslate-next (pdf2zh-next)",
        category="PDF / paper translation",
        binary="pdf2zh_next",
        aliases=["pdfmathtranslate", "pdfmathtranslate-next", "pdf2zh_next"],  # `pdf2zh` is a legacy alias, see LEGACY_ALIASES
        repo="https://github.com/PDFMathTranslate/PDFMathTranslate-next",
        summary="Translate papers to Traditional Chinese PDFs (BabelDOC engine), keeping math, layout and figures",
        why_useful="Successor of pdf2zh, built on BabelDOC: translates arXiv papers into Traditional Chinese (zh-TW) without wrecking LaTeX formulas, two-column layout or figure placement. Produces a translated-only PDF, a side-by-side bilingual PDF, and (default) one PDF with all Chinese pages first, then all original English pages.",
        install_cmd=PDF2ZH_NEXT_INSTALL_CMD,
        usage_examples=[
            "gkey pdf2zh-next paper.pdf                 # Traditional Chinese pages, then the original pages",
            "gkey pdf2zh-next paper.pdf --layout dual   # keep the tool's own outputs only (no merged PDF)",
            "gkey pdf2zh-next paper.pdf --lang-out ja   # other target language (zh-TW is the default)",
            "gkey pdf2zh-next paper.pdf --pages 1-5     # pages 1-5 only",
            "gkey pdf2zh-next paper.pdf --qps 8         # 8 requests/s and workers (gkey grows its worker pool to match)",
            "gkey -m gemini-4-argon-medium pdf2zh-next paper.pdf",
            "gkey pdf2zh paper.pdf                      # old name: still works, runs pdf2zh-next",
        ],
        default_prompt_hint="Append a PDF path, e.g. paper.pdf",
        auto_adapt_note=(
            "Appends `--openai --openai-model <model> --openai-base-url <gkey proxy> --openai-api-key <placeholder>`, "
            "`--lang-out zh-TW` and `--qps 16 --pool-max-workers 16` unless you pass them (a `--qps` alone also sets the workers; "
            "another engine flag such as `--deepseek` or `--siliconflowfree` skips the OpenAI flags). "
            "With `--pages`, `--only-include-translated-page` is added so the merged file pairs the same pages. "
            "Outputs in the current directory (or --output): <name>.zh-TW.mono.pdf (translated), <name>.zh-TW.dual.pdf (side by side) and, "
            "with the default `--layout zh-then-en`, <name>.zh-TW-then-en.pdf (all Chinese pages, then all original pages). "
            "`--layout dual` skips the merged PDF. The old `pdf2zh` name maps here. The install pins `bitstring<5` "
            "(bitstring 5 breaks BabelDOC) and uses `uv tool install --force` because pdf2zh-next also ships a `pdf2zh` executable."
        ),
    ),
    ToolSpec(
        id="babeldoc",
        name="BabelDOC",
        category="PDF / paper translation",
        binary="babeldoc",
        aliases=[],
        repo="https://github.com/funstory-ai/BabelDOC",
        summary="Next-generation, high-precision bilingual PDF layout translation engine",
        why_useful="The successor engine to PDFMathTranslate, better at complex cross-column paragraphs and academic layouts.",
        # bitstring 5.x removed BitStream, which BabelDOC imports: a plain install crashes at import.
        install_cmd='uv tool install --with "bitstring<5" BabelDOC',
        usage_examples=[
            "gkey babeldoc paper.pdf",
            "gkey babeldoc --files paper.pdf --lang-out ja   # Traditional Chinese (zh-TW) is the default",
            "gkey babeldoc paper.pdf --qps 8 --pool-max-workers 24   # gkey grows its worker pool to match (cap 16)",
            "gkey babeldoc paper.pdf --no-auto-extract-glossary      # skip the extra glossary LLM pass (faster)",
        ],
        default_prompt_hint="Append a PDF path, e.g. paper.pdf",
        auto_adapt_note=(
            "Rewrites a positional PDF to `--files` and injects `--openai --openai-model --openai-base-url --openai-api-key`. "
            "Adds `--lang-out zh-TW` unless you pass `--lang-out` or `-lo` (BabelDOC's own default is `zh`, Simplified Chinese). "
            "Speed: default is `--qps 4`; `--qps`/`-q` and `--pool-max-workers` grow gkey's pool. "
            "Fast profile: `--qps 8 --pool-max-workers 24 --no-auto-extract-glossary` (the glossary pass costs one extra LLM sweep over the whole document; "
            "terminology consistency is slightly lower without it). Paragraph batching is built in and not configurable."
        ),
        fast={"flags": ["--qps", "8", "--pool-max-workers", "24", "--no-auto-extract-glossary"], "env": {}, "pool": 16},
    ),
    ToolSpec(
        id="markitdown",
        name="MarkItDown (Microsoft)",
        category="Document to Markdown",
        binary="markitdown",
        aliases=[],
        repo="https://github.com/microsoft/markitdown",
        summary="Convert PDF, PPTX, DOCX, XLSX and images to clean Markdown (local, no model calls)",
        why_useful="Turns messy office documents (PDF, PPTX, DOCX, XLSX, HTML, ZIP) into clean Markdown in one command. The CLI is a purely local converter: it never calls a model.",
        install_cmd='uv tool install "markitdown[all]"',
        usage_examples=[
            "gkey markitdown input.pdf -o output.md",
            "gkey markitdown slides.pptx -o slides.md",
            "gkey markitdown report.docx",
        ],
        default_prompt_hint="Append an input file and optional output, e.g. input.pdf -o output.md",
        auto_adapt_note=(
            "Nothing to adapt or tune: the markitdown CLI makes no LLM calls (llm_client is only available through its Python API), "
            "reads no OPENAI_* variables and has no thread/batch flags, so gkey just runs it. Speed is local CPU/IO; "
            "for many files run several processes yourself (one input per process), e.g. with `xargs -P`. "
            "Because it never calls a model, gkey does not start the proxy for it."
        ),
        no_model=True,
    ),
    ToolSpec(
        id="marker_single",
        name="Marker",
        category="Document to Markdown",
        binary="marker_single",
        aliases=["marker"],
        repo="https://github.com/VikParuchuri/marker",
        summary="High-accuracy PDF to Markdown for scans, tables and math",
        why_useful="Combines local layout models with an LLM pass to handle cross-page tables and complex LaTeX with the best accuracy.",
        install_cmd="uv tool install marker-pdf",
        usage_examples=[
            "gkey marker_single paper.pdf",
            "gkey marker_single paper.pdf --output_dir ./md_out",
            "gkey marker_single paper.pdf --max_concurrency 8 --timeout 120   # gkey grows its worker pool to match",
        ],
        default_prompt_hint="Append a PDF path, e.g. paper.pdf",
        auto_adapt_note=(
            "Injects `--use_llm --llm_service marker.services.openai.OpenAIService --openai_api_key --openai_base_url --openai_model`. "
            "Marker defaults are `--max_concurrency 3 --timeout 30`; the fast profile uses `--max_concurrency 8 --timeout 120` "
            "(30s is easily exceeded by image requests; after the retries a block silently loses its LLM result). "
            "Marker sends images plus a JSON-schema `response_format`, so the backend must support both."
        ),
        fast={"flags": ["--max_concurrency", "8", "--timeout", "120"], "env": {}, "pool": 8},
    ),
    ToolSpec(
        id="gpt-subtrans",
        name="llm-subtrans (gpt-subtrans)",
        category="Subtitles / e-books / audio",
        binary="gpt-subtrans",
        aliases=["llm-subtrans"],
        repo="https://github.com/machinewrapped/llm-subtrans",
        summary="Context-aware .srt / .ass subtitle translation that keeps timing intact",
        why_useful="Translates existing subtitle files with a sliding context window, giving fluent output while keeping timestamps aligned.",
        install_cmd=(
            "git clone --depth 1 https://github.com/machinewrapped/llm-subtrans.git $GKEY_HOME/tools/llm-subtrans/repo && "
            "uv venv $GKEY_HOME/tools/llm-subtrans/venv && "
            "uv pip install --python $GKEY_HOME/tools/llm-subtrans/venv/bin/python -e $GKEY_HOME/tools/llm-subtrans/repo openai"
        ),
        source_install={
            "repo": "https://github.com/machinewrapped/llm-subtrans.git",
            "dir": "llm-subtrans",
            "script": "scripts/gpt-subtrans.py",
            "pip": ["openai"],
        },
        usage_examples=[
            "gkey gpt-subtrans movie.srt -o movie.zh.srt",
            "gkey gpt-subtrans movie.srt -l 'Japanese' -o movie.ja.srt",
            "gkey gpt-subtrans movie.srt --maxbatchsize 100 --scenethreshold 120 -o movie.zh.srt",
        ],
        default_prompt_hint="Append a subtitle path, e.g. input.srt -o output.srt",
        auto_adapt_note=(
            "llm-subtrans 1.7+ ships no console script, so gkey clones it into `$GKEY_HOME/tools/llm-subtrans` (venv included) and runs "
            "`python scripts/gpt-subtrans.py` from that checkout (it needs the checkout's `instructions/`); input/output paths are made absolute first. "
            "Passes `-b <gkey proxy>` (gpt-subtrans always sends its own --apibase default of api.openai.com, which would otherwise beat the "
            "environment), `-k`, `-m gpt-4o-mini` (a non-reasoning name, so it uses /v1/chat/completions; the proxy maps it to your gkey model; "
            "other names are treated as reasoning models and call /v1/responses, which gkey does not serve) and "
            "`-l <GKEY_TARGET_LANG>` (default: Traditional Chinese) unless you give them. "
            "The CLI translates batches sequentially (its MAX_THREADS only applies to the GUI), so the only speed levers are larger/fewer "
            "batches: `--maxbatchsize` (default 100) and `--scenethreshold` (default 60s; batches never cross scenes). "
            "Run several files in parallel as separate processes."
        ),
        fast={"flags": ["--maxbatchsize", "100", "--scenethreshold", "120"], "env": {}, "pool": 4},
        batch_flag="--maxbatchsize",
    ),
    ToolSpec(
        id="bbook_maker",
        name="bilingual_book_maker",
        category="Subtitles / e-books / audio",
        binary="bbook_maker",
        aliases=["bilingual_book_maker"],
        repo="https://github.com/yihong0618/bilingual_book_maker",
        summary="Turn a foreign-language .epub into a bilingual .epub",
        why_useful="Keeps chapter structure and illustrations and produces paragraph-aligned bilingual books. `--test` translates only the first few paragraphs as a preview.",
        # the PyPI project is `bbook_maker` (there is no `bilingual_book_maker` package)
        install_cmd="uv tool install bbook_maker",
        usage_examples=[
            "gkey bbook_maker book.epub",
            "gkey bbook_maker book.epub --test              # preview the first few paragraphs",
            "gkey bbook_maker --book_name book.epub --language 'Japanese'",
            "gkey bbook_maker book.epub --parallel-workers 4   # translate 4 chapters at once (gkey grows its worker pool to match)",
        ],
        default_prompt_hint="Append an e-book path, e.g. book.epub --test",
        auto_adapt_note=(
            "Turns `book.epub` into `--book_name book.epub` and adds `--key`, `--api_base`, `-m <gkey model>`, "
            "`--language <GKEY_TARGET_LANG>` unless you pass them. Fast profile: `--parallel-workers 4` (chapter-level parallelism; "
            "refused with `--use_context session`, and it stops the run when combined with `--resume` and `--accumulated_num` above 1, "
            "so the fast profile skips itself if `--resume` or `--use_context` is present)."
        ),
        fast={"flags": ["--parallel-workers", "4"], "env": {}, "pool": 4, "skip_if_any": ["--resume", "--use_context"]},
    ),
    ToolSpec(
        id="epub_to_audiobook",
        name="epub_to_audiobook",
        category="Subtitles / e-books / audio",
        binary="epub_to_audiobook",
        aliases=[],
        repo="https://github.com/p0n1/epub_to_audiobook",
        summary="Convert an .epub into an audiobook with chapter bookmarks",
        why_useful="Parses EPUB chapters and speaks them through gkey's local /v1/audio/speech endpoint (macOS only: uses `say`).",
        install_cmd='uv tool install "git+https://github.com/p0n1/epub_to_audiobook.git"',
        usage_examples=[
            "gkey epub_to_audiobook book.epub ./audiobook_out",
            "gkey epub_to_audiobook book.epub ./audiobook_out --worker_count 4 --no_prompt",
        ],
        default_prompt_hint="Append <input.epub> <output-dir>, e.g. book.epub ./out",
        auto_adapt_note=(
            "Appends `--tts openai`; speech is served by gkey's built-in /v1/audio/speech (macOS only). "
            "`--worker_count N` converts N chapters in parallel processes (default 1; fast profile uses 4). "
            "Without `--no_prompt` the tool stops at a `Do you want to continue? (y/n)` question after its cost estimate, "
            "so pass `--no_prompt` for unattended runs."
        ),
        fast={"flags": ["--worker_count", "4"], "env": {}, "pool": 4},
    ),
    ToolSpec(
        id="videocaptioner",
        name="VideoCaptioner",
        category="Subtitles / e-books / audio",
        binary="videocaptioner",
        aliases=["videolingo"],
        repo="https://github.com/WEIFENG2333/VideoCaptioner",
        summary="LLM-powered sentence splitting and bilingual subtitles for Whisper output",
        why_useful="Re-segments choppy speech-recognition subtitles into natural single-line bilingual subtitles.",
        # PyPI package videocaptioner needs Python < 3.13; it also imports httpx without declaring it
        install_cmd="uv tool install --python 3.12 --with httpx videocaptioner",
        usage_examples=[
            "gkey videocaptioner subtitle in.srt --translator llm --target-language zh-Hant -o out.srt",
            "gkey videocaptioner subtitle in.srt --thread-num 8 --batch-size 30   # gkey grows its worker pool to match",
            "gkey videocaptioner --help",
        ],
        default_prompt_hint="Append a subcommand, e.g. subtitle in.srt --translator llm --target-language zh-Hant",
        auto_adapt_note=(
            "Runs the real `videocaptioner` CLI with OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL pointing at gkey "
            "(videocaptioner reads these itself). Translation is off unless you pass --translator or --target-language "
            "(use `--translator llm` to translate through gkey; the default translator is Bing). "
            "Fast profile for the `subtitle` and `process` subcommands only: `--thread-num 8 --batch-size 30` (defaults 4 and 20). "
            "With no subcommand the tool opens its GUI."
        ),
        fast={"flags": ["--thread-num", "8", "--batch-size", "30"], "env": {}, "pool": 8, "subcommands": ["subtitle", "process"]},
    ),
    ToolSpec(
        id="fabric",
        name="Fabric",
        category="UNIX pipes / batch",
        binary="fabric",
        aliases=["fabric-ai"],
        repo="https://github.com/danielmiessler/fabric",
        summary="Hundreds of ready-made prompt patterns for UNIX pipelines",
        why_useful="One line like `cat paper.md | gkey fabric -p extract_wisdom` summarizes papers, meetings, logs and videos.",
        install_cmd="go install github.com/danielmiessler/fabric/cmd/fabric@latest",
        usage_examples=[
            "cat article.md | gkey fabric -p summarize",
            "cat meeting.txt | gkey fabric -p extract_wisdom",
            "gkey fabric -L                                  # list all built-in patterns",
        ],
        default_prompt_hint="Append fabric args, e.g. -L (list patterns) or -p summarize",
        auto_adapt_note="Uses fabric's LiteLLM vendor (Chat Completions; the OpenAI vendor calls /v1/responses, which gkey does not serve) via `DEFAULT_VENDOR`, `LITELLM_API_KEY`, `LITELLM_API_BASE_URL`; creates `~/.config/fabric/.env` if missing.",
        fast={"flags": ["--maxTokens", "4096"], "env": {}, "pool": 4},
    ),
    ToolSpec(
        id="mods",
        name="Mods (Charmbracelet)",
        category="UNIX pipes / batch",
        binary="mods",
        aliases=[],
        repo="https://github.com/charmbracelet/mods",
        summary="Minimal LLM for UNIX pipes: git diff, logs, CSV/JSON",
        why_useful="Pipe `git diff`, `error.log` or JSON into it and get clean Markdown back; a stable choice for shell scripts.",
        install_cmd="brew install charmbracelet/tap/mods || go install github.com/charmbracelet/mods@latest",
        usage_examples=[
            'git diff | gkey mods "summarize this change and flag potential bugs"',
            'cat error.log | gkey mods "find the root cause and list fix commands"',
            'gkey mods "compare Nix and Homebrew in a table"',
        ],
        default_prompt_hint='Append a prompt, e.g. "list handy macOS network debugging commands"',
        auto_adapt_note="Writes `mods/mods.yml` under `~/.gkey/xdg` (base-url points at gkey) and sets `XDG_CONFIG_HOME` to it, because mods only reads its settings from the XDG config dir.",
        fast={"flags": ["--no-cache", "--max-retries", "2"], "env": {}, "pool": 4},
    ),
    ToolSpec(
        id="ai-renamer",
        name="ai-renamer",
        category="UNIX pipes / batch",
        binary="ai-renamer",
        aliases=[],
        repo="https://github.com/ozgrozer/ai-renamer",
        summary="Rename screenshots, images and PDFs based on their content",
        why_useful="Cleans up folders full of `IMG_0293.png` and `scan_01.pdf` by reading the content and giving each file a readable name.",
        install_cmd="npm install -g ai-renamer",
        usage_examples=[
            "gkey ai-renamer ~/Desktop/screenshots",
            "gkey ai-renamer ~/Desktop --language='English'",
        ],
        default_prompt_hint="Append a folder to rename, e.g. ./screenshots",
        auto_adapt_note="Injects `--provider=openai --api-key=... --base-url=http://127.0.0.1:PORT --model=...` (no /v1: the tool appends it) and uses a private HOME so your own `~/ai-renamer.json` is not overwritten.",
        fast={"flags": ["--frames", "2"], "env": {}, "pool": 4},
    ),
    ToolSpec(
        id="oco",
        name="OpenCommit (oco)",
        category="Git / i18n",
        binary="oco",
        aliases=["opencommit"],
        repo="https://github.com/di-sukharev/opencommit",
        summary="Generate commit messages from your staged diff in seconds",
        why_useful="After `git add`, run `gkey oco` to turn the staged diff into a well-formed commit message.",
        install_cmd="npm install -g opencommit",
        usage_examples=[
            "gkey oco",
            "gkey oco --fgm                                  # full GitMoji spec",
        ],
        default_prompt_hint="Press Enter to run oco in the current git repo",
        auto_adapt_note="Sets `OCO_AI_PROVIDER=openai`, `OCO_API_URL`, `OCO_API_KEY`, `OCO_MODEL`.",
        fast={"flags": [], "env": {"OCO_TOKENS_MAX_INPUT": "32768", "OCO_TOKENS_MAX_OUTPUT": "500"}, "pool": 4},
    ),
    ToolSpec(
        id="gptcommit",
        name="gptcommit (Rust)",
        category="Git / i18n",
        binary="gptcommit",
        aliases=[],
        repo="https://github.com/zurawiki/gptcommit",
        summary="Fast Rust commit-message generator and git hook",
        why_useful="Native binary, handles large diffs concurrently, and can be installed as a `prepare-commit-msg` hook.",
        install_cmd="brew install zurawiki/brews/gptcommit || cargo install --locked gptcommit",
        usage_examples=[
            "gkey gptcommit                                  # show gptcommit's subcommands",
            "gkey gptcommit install                          # install the git hook in this repo",
            "gkey git commit                                 # the hook inherits gkey's env, so it talks to the proxy",
        ],
        default_prompt_hint="Append a subcommand: install | uninstall | config | prepare-commit-msg",
        auto_adapt_note="Sets `GPTCOMMIT__OPENAI__API_BASE`, `GPTCOMMIT__OPENAI__API_KEY`, `GPTCOMMIT__OPENAI__MODEL`. gptcommit needs a subcommand; with none gkey shows its help. The installed git hook runs plain `gptcommit`, so commit via `gkey git commit` for it to use the proxy.",
        fast={"flags": [], "env": {"GPTCOMMIT__OPENAI__RETRIES": "1"}, "pool": 4},
    ),
    ToolSpec(
        id="lobe-i18n",
        name="lobe-i18n (LobeHub)",
        category="Git / i18n",
        binary="lobe-i18n",
        aliases=[],
        repo="https://github.com/lobehub/lobe-cli-toolbox",
        summary="Batch-translate locale JSON files or Markdown into many languages",
        why_useful="Keeps a project's locale files in sync with a sliding-window translator: `gkey lobe-i18n` or `gkey lobe-i18n md`.",
        install_cmd="npm install -g @lobehub/i18n-cli",
        usage_examples=[
            "gkey lobe-i18n",
            "gkey lobe-i18n md                               # translate Markdown docs",
        ],
        default_prompt_hint="Press Enter to run lobe-i18n (or add `md` for Markdown)",
        auto_adapt_note="Sets `OPENAI_PROXY_URL` and `OPENAI_API_KEY`, and passes `-c` with a merged copy of your JSON config (`.i18nrc`, `.i18nrc.json`, package.json `i18n`) whose `modelName` is a supported model; `concurrency`/`splitToken` come from `GKEY_LOBE_CONCURRENCY`/`GKEY_LOBE_SPLIT_TOKEN`.",
        fast={"flags": [], "env": {"GKEY_LOBE_CONCURRENCY": "8", "GKEY_LOBE_SPLIT_TOKEN": "3000"}, "pool": 8},
        batch_flag=None,
    ),
]

# Speed contract kept outside the ToolSpec entries (additive; a ToolSpec attribute of the same
# name wins). Keys per tool id: fast={"flags": [...], "env": {...}, "pool": N, "aliases": {...}},
# batch_flag="--flag", per_file={"value_flags": [...]}. See gkey_speed.py.
SPEED: dict = {
    "pdf2zh-next": {
        # gkey already injects --qps/--pool-max-workers 16 when neither is given (speed options run first, then adapt_args), so this
        # profile mostly just sizes the pool; a user-given --qps/--pool-max-workers wins over both (see CONCURRENCY_FLAGS).
        "fast": {"flags": ["--qps", "16", "--pool-max-workers", "16"], "pool": 16},
    },
}

TOOL_BY_ID: dict = {}
for _t in TOOLS:
    TOOL_BY_ID[_t.id] = _t
    TOOL_BY_ID[_t.binary] = _t
    for _alias in _t.aliases:
        TOOL_BY_ID[_alias] = _t
for _old, _new in LEGACY_ALIASES.items():
    TOOL_BY_ID[_old] = TOOL_BY_ID[_new]


_SUBTRANS_MODEL = "gpt-4o-mini"  # not a reasoning model for llm-subtrans (chat completions); the proxy maps it to the gkey model


def source_paths(spec: ToolSpec) -> Optional[dict]:
    """Checkout/venv/script paths of a source-installed tool, or None for normal tools."""
    si = spec.source_install
    if not si:
        return None
    root = gkey_home() / "tools" / si["dir"]
    return {"root": root, "repo": root / "repo", "python": root / "venv" / "bin" / "python",
            "script": root / "repo" / si["script"]}


def _install_source_tool(spec: ToolSpec) -> bool:
    paths = source_paths(spec)
    si = spec.source_install
    steps = []
    if not paths["repo"].is_dir():
        steps.append(["git", "clone", "--depth", "1", si["repo"], str(paths["repo"])])
    if not paths["python"].exists():
        steps.append(["uv", "venv", str(paths["root"] / "venv")])
    steps.append(["uv", "pip", "install", "--python", str(paths["python"]), "-e", str(paths["repo"]), *si.get("pip", [])])
    paths["root"].mkdir(parents=True, exist_ok=True)
    for cmd in steps:
        sys.stderr.write(f"[gkey] running: {' '.join(cmd)}\n")
        try:
            rc = subprocess.run(cmd).returncode
        except FileNotFoundError:
            sys.stderr.write(f"[gkey] `{cmd[0]}` not found; install it first.\n")
            return False
        if rc != 0:
            sys.stderr.write(f"\n[gkey] Failed to install {spec.name} (step `{' '.join(cmd[:2])}` exit code {rc}).\n")
            return False
    return True


def is_tool_installed(spec: ToolSpec) -> Optional[str]:
    ensure_extended_path()
    paths = source_paths(spec)
    if paths:
        return str(paths["script"]) if paths["python"].exists() and paths["script"].is_file() else None
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
    if is_tool_installed(spec):
        return True

    err = sys.stderr
    err.write(f"\n[gkey] {spec.name} ({spec.binary}) is not installed; installing it now...\n")
    if spec.source_install:
        if not _install_source_tool(spec):
            return False
        err.write(f"\n[gkey] {spec.name} installed.\n\n")
        return True
    err.write(f"[gkey] running: {spec.install_cmd}\n\n")
    err.flush()

    res = subprocess.run(spec.install_cmd, shell=True)
    if res.returncode != 0:
        err.write(f"\n[gkey] Failed to install {spec.name} (exit code {res.returncode}).\n")
        err.write(f"[gkey] You can install it manually: {spec.install_cmd}\n")
        return False

    ensure_extended_path()
    err.write(f"\n[gkey] {spec.name} installed.\n\n")
    return True


def _atomic_write(path: pathlib.Path, text: str) -> None:
    """Write via a temp file + rename so concurrent readers never see a truncated file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


_FABRIC_MARK = "# gkey-managed"


def _ensure_fabric_config(base_url: str, api_key: str, model: str) -> None:
    """Create fabric's .env for the LiteLLM vendor (the OpenAI vendor uses /v1/responses: 404 on gkey).

    A user-written .env is never touched; a stale gkey-written one (marker, or the legacy OpenAI
    layout pointing at 127.0.0.1) is refreshed. Real env vars win over .env anyway.
    """
    fabric_dir = pathlib.Path.home() / ".config" / "fabric"
    fabric_dir.mkdir(parents=True, exist_ok=True)
    env_file = fabric_dir / ".env"
    if env_file.exists():
        old = env_file.read_text(encoding="utf-8", errors="replace")
        legacy = "DEFAULT_VENDOR=OpenAI" in old and "OPENAI_API_BASE_URL=http://127.0.0.1" in old
        if _FABRIC_MARK not in old and not legacy:
            return
    _atomic_write(
        env_file,
        f"{_FABRIC_MARK}\n"
        f"DEFAULT_VENDOR=LiteLLM\n"
        f"DEFAULT_MODEL={model}\n"
        f"LITELLM_API_KEY={api_key}\n"
        f"LITELLM_API_BASE_URL={base_url}\n",
    )


def _fabric_patterns_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "fabric" / "patterns"


def _ensure_fabric_patterns(args: List[str], env: dict) -> None:
    """Download fabric's patterns once (`fabric -U`): a fresh install has none and `-p <name>` fails.

    Skipped when the patterns dir already has content, or the user is running -U/-S/-h themselves.
    A failed download only warns (offline use of already-present patterns keeps working).
    """
    if any(a in ("-U", "--updatepatterns", "-S", "--setup", "-h", "--help") for a in args):
        return
    pdir = _fabric_patterns_dir()
    if pdir.is_dir() and any(pdir.iterdir()):
        return
    sys.stderr.write("[gkey] fabric has no patterns yet; downloading them once (fabric -U)...\n")
    try:
        # fabric prints progress on stdout: keep it off the pipe the user may be reading, and never let it eat piped input
        rc = subprocess.run(["fabric", "-U"], env={**os.environ, **env}, stdout=sys.stderr,
                            stdin=subprocess.DEVNULL).returncode
    except OSError as exc:
        sys.stderr.write(f"[gkey] could not run `fabric -U`: {exc}\n")
        return
    if rc != 0:
        sys.stderr.write(f"[gkey] `fabric -U` failed (exit {rc}); run it manually if patterns are missing.\n")


def _ensure_mods_config(base_url: str, model: str) -> pathlib.Path:
    """Write `<GKEY_HOME>/xdg/mods/mods.yml` pointing at gkey; return the XDG config home.

    mods only reads settings from xdg.ConfigFile("mods/mods.yml"), so the caller must export
    XDG_CONFIG_HOME=<returned dir>. The file is rewritten atomically, and only when it changed.
    """
    xdg_home = gkey_home() / "xdg"
    mods_dir = xdg_home / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    mods_yml = mods_dir / "mods.yml"
    aliases = ["default", "gpt-4o-mini", "gpt-4o"]
    if model in aliases:  # the model key may not also be an alias / appear twice (yaml.v3 rejects duplicates)
        aliases.remove(model)
    text = (
        f"default-api: openai\n"
        f"default-model: {model}\n"
        f"apis:\n"
        f"  openai:\n"
        f"    base-url: {base_url}\n"
        f"    api-key-env: OPENAI_API_KEY\n"
        f"    models:\n"
        f"      {model}:\n"
        f"        aliases: [{', '.join(chr(34) + a + chr(34) for a in aliases)}]\n"
        f"        max-input-chars: 392000\n"
    )
    try:
        if mods_yml.read_text(encoding="utf-8") == text:
            return xdg_home
    except OSError:
        pass
    _atomic_write(mods_yml, text)
    return xdg_home


_LOBE_CONFIG_NAMES = (".i18nrc", ".i18nrc.json")
_LOBE_MODEL = "gpt-4o-mini"  # must be a key of lobe-i18n's ModelTokens, else splitToken is NaN


def _find_lobe_config(start: pathlib.Path):
    """Search upward like cosmiconfig for a JSON lobe-i18n config. Returns (path, dict, is_pkg) or None."""
    import json

    for d in [start, *start.parents]:
        for name in _LOBE_CONFIG_NAMES:
            f = d / name
            if f.is_file():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except ValueError:
                    return None  # YAML in a plain .i18nrc: leave it to lobe-i18n
                return (f, data, False) if isinstance(data, dict) else None
        pkg = d / "package.json"
        if pkg.is_file():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8")).get("i18n")
            except (ValueError, AttributeError):
                data = None
            if isinstance(data, dict):
                return pkg, data, True
        if any((d / n).is_file() for n in (".i18nrc.js", ".i18nrc.cjs", ".i18nrc.mjs", ".i18nrc.yaml", ".i18nrc.yml",
                                           "i18n.config.js", "i18n.config.cjs", "i18n.config.mjs")):
            return None  # non-JSON config: do not try to merge
    return None


def _lobe_int(name: str, env: Optional[dict] = None) -> Optional[int]:
    """GKEY_LOBE_* value from `env` (the child env being built, e.g. the --fast profile) or os.environ."""
    v = (env or {}).get(name) or os.environ.get(name, "")
    return int(v) if v.isdigit() and int(v) > 0 else None


def _ensure_lobe_config(cwd: Optional[pathlib.Path] = None, env: Optional[dict] = None) -> Optional[pathlib.Path]:
    """Write a merged copy of the project's JSON config with a supported modelName (and optional
    concurrency / splitToken); return its path, or None when no JSON config was found."""
    import json

    found = _find_lobe_config(cwd or pathlib.Path.cwd())
    if not found:
        return None
    src, cfg, _is_pkg = found
    cfg = dict(cfg)
    cfg["modelName"] = _LOBE_MODEL
    # Without this lobe-i18n only writes the output file at the very end and leaves `{}` if anything goes wrong.
    cfg["saveImmediately"] = True
    conc, split = _lobe_int("GKEY_LOBE_CONCURRENCY", env), _lobe_int("GKEY_LOBE_SPLIT_TOKEN", env)
    if conc:
        cfg["concurrency"] = conc
    if split:
        cfg["splitToken"] = split
    out_dir = gkey_home() / "lobe-i18n"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"i18nrc-{hashlib.sha1(str(src).encode()).hexdigest()[:10]}.json"
    _atomic_write(out, json.dumps(cfg, indent=2) + "\n")
    return out


# ---- pdf2zh-next: engine/target flags, merged output layout, post-processing --------------------

PDF2ZH_DEFAULT_LANG_OUT = "zh-TW"  # BabelDOC maps zh-TW to its Traditional Chinese font and prompt
PDF2ZH_LAYOUTS = ("zh-then-en", "dual")
# Flags that choose the translation engine; when one is present gkey does not inject the OpenAI flags.
_PDF2ZH_ENGINE_FLAGS = {
    "--siliconflowfree", "--openai", "--deepseek", "--ollama", "--xinference", "--azureopenai",
    "--openaicompatible", "--claudecode", "--clitranslator",
}
# Options that take a separate value (so the value is not mistaken for an input file by --parallel-files).
_PDF2ZH_VALUE_FLAGS = {
    "--pages", "--lang-in", "--lang-out", "--output", "--qps", "--pool-max-workers", "--openai-model",
    "--openai-base-url", "--openai-api-key", "--config", "--min-text-length", "--rpc-doclayout",
    "--watermark-output-mode", "--working-dir",
}
SPEED["pdf2zh-next"]["per_file"] = {"value_flags": sorted(_PDF2ZH_VALUE_FLAGS)}
_PDF2ZH_QPS_FLAGS = ("--qps",)
_PDF2ZH_WORKER_FLAGS = ("--pool-max-workers",)


def _has_flag(args: List[str], *names: str) -> bool:
    return any(a == n or a.startswith(n + "=") for a in args for n in names)


def split_pdf2zh_layout(args: List[str]) -> "tuple[List[str], str]":
    """Remove gkey's own `--layout <zh-then-en|dual>` from args; return (args, layout)."""
    out: List[str] = []
    layout = "zh-then-en"
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--layout" or a.startswith("--layout="):
            if a == "--layout":
                if i + 1 >= len(args):
                    sys.stderr.write("[gkey] --layout needs a value: zh-then-en or dual\n")
                    sys.exit(2)
                layout = args[i + 1]
                i += 1
            else:
                layout = a.split("=", 1)[1]
            if layout not in PDF2ZH_LAYOUTS:
                sys.stderr.write(f"[gkey] Invalid --layout '{layout}': use zh-then-en or dual\n")
                sys.exit(2)
        else:
            out.append(a)
        i += 1
    return out, layout


def parse_pdf2zh_args(args: List[str]) -> dict:
    """Pull the bits the post-step needs (inputs, output dir, languages, pages) out of pdf2zh-next args.

    A token after a bare `--flag` is that flag's value unless it looks like an input (a .pdf name
    or an existing path); every other non-flag token is an input.
    """
    info = {"files": [], "output": "", "lang_in": "en", "lang_out": PDF2ZH_DEFAULT_LANG_OUT, "pages": ""}
    keys = {"--output": "output", "--lang-in": "lang_in", "--lang-out": "lang_out", "--pages": "pages"}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            if k in keys:
                info[keys[k]] = v
        elif a.startswith("-") and len(a) > 1:
            nxt = args[i + 1] if i + 1 < len(args) else None
            is_input = nxt is not None and (nxt.lower().endswith(".pdf") or os.path.exists(nxt))
            takes_value = nxt is not None and not nxt.startswith("-") and not is_input
            if a in keys and takes_value:
                info[keys[a]] = nxt
            if takes_value and a not in _PDF2ZH_ENGINE_FLAGS and not a.startswith(("--no-", "--enable-", "--disable-")):
                i += 1
        else:
            info["files"].append(a.strip("\"'"))
        i += 1
    return info


def pdf2zh_python(binary: str) -> Optional[str]:
    """Interpreter of pdf2zh-next's own (uv tool) environment: it has PyMuPDF; gkey's venv does not."""
    path = shutil.which(binary)
    if path:
        try:
            with open(path, "rb") as fh:
                first = fh.readline().decode("utf-8", "replace").strip()
            if first.startswith("#!"):
                parts = first[2:].strip().split()
                if parts and os.path.exists(parts[0]) and not parts[0].endswith("env"):
                    return parts[0]
        except OSError:
            pass
    return None


def pdf2zh_output_paths(outdir: str, stem: str, lang_out: str) -> "tuple[str, str]":
    """BabelDOC output names: <stem>.<lang-out>.mono.pdf and <stem>.<lang-out>.dual.pdf."""
    return (os.path.join(outdir, f"{stem}.{lang_out}.mono.pdf"), os.path.join(outdir, f"{stem}.{lang_out}.dual.pdf"))


def pdf2zh_postprocess(binary: str, args: List[str], layout: str) -> None:
    """After a successful pdf2zh-next run: build the merged PDF and fix MuPDF-breaking numbers."""
    py = pdf2zh_python(binary)
    err = sys.stderr
    if not py:
        err.write("[gkey] Could not locate pdf2zh-next's Python; skipping the post-step (outputs are the plain files).\n")
        return
    info = parse_pdf2zh_args(args)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_post.py")
    outdir = info["output"] or os.getcwd()
    for f in info["files"]:
        if not f.lower().endswith(".pdf") or not os.path.isfile(f):
            continue
        stem = os.path.splitext(os.path.basename(f))[0]
        mono, dual = pdf2zh_output_paths(outdir, stem, info["lang_out"])
        if layout == "zh-then-en" and os.path.isfile(mono):
            merged = os.path.join(outdir, f"{stem}.{info['lang_out']}-then-{info['lang_in']}.pdf")
            cmd = [py, "-I", script, "zh-then-en", mono, f, merged] + ([info["pages"]] if info["pages"] else [])
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                err.write(f"[gkey] Merged PDF ({res.stdout.strip()}): {merged}\n")
            else:
                err.write(f"[gkey] Could not build the merged PDF: {res.stderr.strip()[-300:]}\n")
        elif layout == "zh-then-en":
            err.write(f"[gkey] No translated PDF at {mono}; skipping the merged PDF (was --no-mono given?).\n")
        existing = [p for p in (mono, dual) if os.path.isfile(p)]
        if existing:
            res = subprocess.run([py, "-I", script, "fix"] + existing, capture_output=True, text=True)
            if res.returncode != 0:
                err.write(f"[gkey] Could not post-fix outputs: {res.stderr.strip()[-300:]}\n")


def run_pdf2zh(binary: str, args: List[str], layout: str) -> int:
    """Run pdf2zh-next as a child (not exec) so the post-step can follow."""
    try:
        rc = subprocess.run([binary] + args).returncode
    except FileNotFoundError:
        sys.stderr.write(f"[gkey] Command not found: {binary}\n")
        return 127
    except KeyboardInterrupt:
        return 130
    if rc == 0:
        pdf2zh_postprocess(binary, args, layout)
    return rc


def _has_flag(args: List[str], *names: str) -> bool:
    """True when any of `names` is present as `--x v`, `--x=v` or (single-dash) `-xv`."""
    for a in args:
        for n in names:
            if a == n or a.startswith(n + "="):
                return True
            if not n.startswith("--") and a.startswith(n) and len(a) > len(n) and not a.startswith("--"):
                return True
    return False


def adapt_args(spec: ToolSpec, args: List[str], port: str, model: str, env: dict) -> List[str]:
    """Return the adapted argv tail and mutate `env` with the variables the tool needs.

    Pure apart from the config-file helpers for fabric/mods; unit-testable.
    """
    base_url = f"http://127.0.0.1:{port}/v1"
    api_key = LOCAL_API_KEY
    lang = target_language()
    args = list(args)

    if spec.id == "pdf2zh-next":
        args, layout = split_pdf2zh_layout(args)
        env["GKEY_PDF2ZH_LAYOUT"] = layout
        if not any(_has_flag(args, f) for f in _PDF2ZH_ENGINE_FLAGS):
            args.append("--openai")
        if "--openai" in args:
            if not _has_flag(args, "--openai-model"):
                args.extend(["--openai-model", model])
            if not _has_flag(args, "--openai-base-url"):
                args.extend(["--openai-base-url", base_url])
            if not _has_flag(args, "--openai-api-key"):
                args.extend(["--openai-api-key", api_key])
        if not _has_flag(args, "--lang-out"):
            args.extend(["--lang-out", PDF2ZH_DEFAULT_LANG_OUT])
        has_qps = _has_flag(args, *_PDF2ZH_QPS_FLAGS)
        if not has_qps and not _has_flag(args, *_PDF2ZH_WORKER_FLAGS):
            args.extend(["--qps", str(PDF2ZH_NEXT_FAST_QPS), "--pool-max-workers", str(PDF2ZH_NEXT_FAST_QPS)])
        if _has_flag(args, "--pages") and not _has_flag(args, "--only-include-translated-page"):
            args.append("--only-include-translated-page")

    elif spec.id == "babeldoc":
        if args and "--files" not in args:
            new_args: List[str] = []
            for a in args:
                if not a.startswith("-") and a.lower().endswith(".pdf"):
                    new_args.extend(["--files", a])
                else:
                    new_args.append(a)
            args = new_args
        if "--openai" not in args:
            args.extend(
                ["--openai", "--openai-model", model, "--openai-base-url", base_url, "--openai-api-key", api_key]
            )
        # BabelDOC's own default is `zh` (Simplified); gkey translates to Traditional Chinese unless
        # the user passes --lang-out / -lo (also --lang-out=xx, -lo=xx, -loxx).
        if not _has_flag(args, "--lang-out", "-lo"):
            args.extend(["--lang-out", PDF2ZH_DEFAULT_LANG_OUT])

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
        # gpt-subtrans always passes its own --apibase default (api.openai.com), which beats the env
        if not _has_flag(args, "-b", "--apibase"):
            args.extend(["-b", base_url])
        if not _has_flag(args, "-k", "--apikey"):
            args.extend(["-k", api_key])
        if not _has_flag(args, "-m", "--model"):
            args.extend(["-m", _SUBTRANS_MODEL])
        if not _has_flag(args, "-l", "--target-language"):
            args.extend(["-l", lang])

    elif spec.id == "bbook_maker":
        if args and not args[0].startswith("-") and "--book_name" not in args:
            args = ["--book_name", args[0]] + args[1:]
        if not _has_flag(args, "--key", "--api_key", "--openai_key"):
            args.extend(["--key", api_key])
        if not _has_flag(args, "--api_base"):
            args.extend(["--api_base", base_url])
        if not _has_flag(args, "-m", "--model"):
            args.extend(["-m", model])
        if "--language" not in args:
            args.extend(["--language", lang])

    elif spec.id == "epub_to_audiobook":
        if "--tts" not in args:
            args.extend(["--tts", "openai"])

    elif spec.id == "fabric":
        _ensure_fabric_config(base_url, api_key, model)
        # LiteLLM vendor = Chat Completions only; vendor OpenAI posts to /v1/responses (404 here).
        env["DEFAULT_VENDOR"] = "LiteLLM"
        env["DEFAULT_MODEL"] = model
        env["LITELLM_API_KEY"] = api_key
        env["LITELLM_API_BASE_URL"] = base_url
        _ensure_fabric_patterns(args, env)

    elif spec.id == "mods":
        env["XDG_CONFIG_HOME"] = str(_ensure_mods_config(base_url, model))

    elif spec.id == "ai-renamer":
        # The openai provider requests `${baseURL}/v1/chat/completions`: no /v1 in the base URL.
        root_url = base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url
        if not any(a.startswith("--provider") for a in args):
            args.append("--provider=openai")
        if not any(a.startswith("--api-key") for a in args):
            args.append(f"--api-key={api_key}")
        if not any(a.startswith("--base-url") for a in args):
            args.append(f"--base-url={root_url}")
        if not any(a.startswith("--model") for a in args):
            args.append(f"--model={model}")
        # ai-renamer persists every flag into ~/ai-renamer.json: give it a private HOME instead.
        home = gkey_home() / "ai-renamer-home"
        home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(home)

    elif spec.id == "oco":
        env["OCO_AI_PROVIDER"] = "openai"
        env["OCO_API_KEY"] = api_key
        env["OCO_API_URL"] = base_url
        env["OCO_MODEL"] = "gpt-4o-mini"

    elif spec.id == "gptcommit":
        env["GPTCOMMIT__OPENAI__API_KEY"] = api_key
        env["GPTCOMMIT__OPENAI__API_BASE"] = base_url
        env["GPTCOMMIT__OPENAI__MODEL"] = "gpt-4o-mini"
        if not args:  # clap requires a subcommand: show help instead of a bare usage error
            sys.stderr.write(
                "[gkey] gptcommit needs a subcommand: install | uninstall | config | prepare-commit-msg.\n"
                "[gkey] After `gkey gptcommit install`, commit with `gkey git commit` so the hook uses the gkey proxy.\n"
            )
            args = ["--help"]

    elif spec.id == "lobe-i18n":
        env["OPENAI_PROXY_URL"] = base_url
        env["OPENAI_API_KEY"] = api_key
        if not any(a in ("-c", "--config") or a.startswith("--config=") for a in args):
            merged = _ensure_lobe_config(env=env)
            if merged:
                args = ["-c", str(merged)] + args
            else:
                sys.stderr.write(
                    "[gkey] No JSON lobe-i18n config found (.i18nrc, .i18nrc.json, package.json \"i18n\"); "
                    "make sure its modelName is a model lobe-i18n knows (e.g. gpt-4o-mini).\n"
                )

    return args


# Options of source-run tools whose value is an output path (made absolute before the cwd changes).
_SOURCE_PATH_VALUE_FLAGS = ("-o", "--output", "--instructionfile", "--glossaryfile")


def source_launch(spec: ToolSpec, args: List[str]) -> "tuple[List[str], pathlib.Path]":
    """argv + cwd to run a source-installed tool: its script, from the checkout (it reads `instructions/`
    relative to the cwd). Relative file arguments are made absolute first so they still resolve."""
    paths = source_paths(spec)
    out: List[str] = []
    take_value = False
    for a in args:
        if take_value:
            a, take_value = os.path.abspath(a), False
        elif a in _SOURCE_PATH_VALUE_FLAGS:
            take_value = True
        elif a.startswith("-") and "=" in a and a.split("=", 1)[0] in _SOURCE_PATH_VALUE_FLAGS:
            k, v = a.split("=", 1)
            a = f"{k}={os.path.abspath(v)}"
        elif not a.startswith("-") and os.path.exists(a):
            a = os.path.abspath(a)
        out.append(a)
    return [str(paths["python"]), str(paths["script"])] + out, paths["repo"]


def is_help_run(args: List[str]) -> bool:
    return any(a in ("-h", "--help", "-v", "--version") for a in args)


def apply_speed_options(spec: ToolSpec, args: List[str], env: dict) -> List[str]:
    """--fast / --batch (from GKEY_FAST / GKEY_BATCH): inject per-tool flags the user did not pass."""
    opts = gkey_speed.speed_options()
    if opts["fast"]:
        args, notes = gkey_speed.apply_fast(spec.id, args, gkey_speed.tool_config(spec, "fast", SPEED), env)
        sys.stderr.write(f"[gkey] --fast ({spec.id}): {'; '.join(notes)}\n")
    if opts["batch"]:
        args, note = gkey_speed.apply_batch(args, opts["batch"], gkey_speed.tool_config(spec, "batch_flag", SPEED))
        if note:
            sys.stderr.write(f"[gkey] --batch ({spec.id}): {note}\n")
    return args


def maybe_run_parallel(spec: ToolSpec, binary: str, args: List[str], layout: Optional[str]) -> Optional[int]:
    """--parallel-files N: one run per input file, up to N at once. None = not applicable."""
    n = gkey_speed.speed_options()["parallel"]
    if not n or n < 2:
        return None
    per_file = gkey_speed.tool_config(spec, "per_file", SPEED)
    if not per_file:
        sys.stderr.write(f"[gkey] --parallel-files ({spec.id}): this tool is not marked one-file-per-run; running normally\n")
        return None
    idx = gkey_speed.find_inputs(args, per_file.get("value_flags"))
    if len(idx) < 2:
        sys.stderr.write("[gkey] --parallel-files: fewer than 2 input files found; running normally\n")
        return None
    runs = gkey_speed.build_runs(args, idx)
    labels = gkey_speed.unique_labels(args, idx)
    post = (lambda a: pdf2zh_postprocess(binary, a, layout)) if spec.id == "pdf2zh-next" and layout else None
    sys.stderr.write(f"[gkey] --parallel-files: {len(runs)} runs, up to {n} at once\n")
    return gkey_speed.run_parallel(binary, runs, labels, n, post=post)


def adapt_and_exec(port: str, model: str, argv: List[str]) -> None:
    """Install (if needed), adapt arguments/environment, then exec the target tool."""
    ensure_extended_path()
    if not argv:
        sys.exit(1)

    cmd_name = argv[0]
    args = list(argv[1:])

    base_url = f"http://127.0.0.1:{port}/v1"
    # Never inherit a real OPENAI_API_KEY: the local proxy ignores keys, and we do
    # not want to hand (or print) the user's real key to third-party tools.
    env_updates = {
        "OPENAI_BASE_URL": base_url,
        "OPENAI_API_BASE": base_url,
        "OPENAI_API_BASE_URL": base_url,
        "OPENAI_PROXY_URL": base_url,
        "OPENAI_API_KEY": LOCAL_API_KEY,
        "OPENAI_MODEL": model,
    }

    spec = TOOL_BY_ID.get(cmd_name)
    if spec is not None:
        if not install_tool(spec):
            sys.exit(1)
        if cmd_name in LEGACY_ALIASES:
            sys.stderr.write(f"[gkey] `{cmd_name}` now runs {LEGACY_ALIASES[cmd_name]} (the successor); use `gkey {LEGACY_ALIASES[cmd_name]} ...`.\n")
        cmd_name = spec.binary

        # Help/version flags pass through untouched
        is_help = any(a in ("-h", "--help", "-v", "--version") for a in args)
        if not is_help:
            # Speed first: its env (e.g. --fast's GKEY_LOBE_*) is read by adapt_args through `env_updates`.
            args = apply_speed_options(spec, args, env_updates)
            args = adapt_args(spec, args, port, model, env_updates)

    layout = env_updates.pop("GKEY_PDF2ZH_LAYOUT", None)
    os.environ.update(env_updates)
    if spec is not None and not is_help_run(argv[1:]):
        rc = maybe_run_parallel(spec, cmd_name, args, layout)
        if rc is not None:
            sys.exit(rc)
    if spec is not None and spec.id == "pdf2zh-next" and layout:
        sys.exit(run_pdf2zh(cmd_name, args, layout))
    if spec is not None and spec.source_install:
        argv_exec, cwd = source_launch(spec, args)
        os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(cwd), os.environ.get("PYTHONPATH", "")) if p)
        os.chdir(cwd)
        os.execv(argv_exec[0], argv_exec)
    try:
        os.execvp(cmd_name, [cmd_name] + args)
    except FileNotFoundError:
        sys.stderr.write(f"[gkey] Command not found: {cmd_name}\n")
        sys.exit(127)


def is_installed_label(spec: ToolSpec) -> str:
    return "installed" if is_tool_installed(spec) else "not installed"


def print_list(plain: bool = False) -> None:
    """Human-readable list (default) or tab-separated rows for fzf (plain=True)."""
    for i, spec in enumerate(TOOLS, 1):
        label = is_installed_label(spec)
        if plain:
            print(f"{spec.id}\t{spec.id:<18} {label:<14} {spec.summary}")
        else:
            mark = _color("32", "installed    ") if label == "installed" else _color("33", "not installed")
            print(f"{i:>2}. {spec.id:<18} {mark}  {spec.summary}")


def print_preview(tool_id: str) -> None:
    spec = TOOL_BY_ID.get(tool_id.strip())
    if not spec:
        print(f"Unknown tool: {tool_id}")
        return

    installed_path = is_tool_installed(spec)
    status = f"installed ({installed_path})" if installed_path else "not installed (installed automatically on first run)"

    print(_color("1;36", f"{spec.name}"))
    print(f"{spec.summary}\n")
    print(f"Category : {spec.category}")
    print(f"Project  : {spec.repo}")
    print(f"Status   : {status}\n")
    print(spec.why_useful + "\n")
    print(_color("1", "Examples"))
    for ex in spec.usage_examples:
        print(f"  {ex}")
    print()
    print(_color("1", "What gkey adapts for you"))
    print(f"  {spec.auto_adapt_note}\n")
    print(_color("1", "Speed options (opt-in)"))
    fast = gkey_speed.tool_config(spec, "fast", SPEED)
    if fast:
        bits = [" ".join(fast.get("flags") or [])] + [f"{k}={v}" for k, v in (fast.get("env") or {}).items()]
        print(f"  --fast            adds: {' '.join(b for b in bits if b) or '(nothing)'}"
              + (f"; proxy pool >= {fast['pool']}" if fast.get("pool") else "") + " (your own flags always win)")
    else:
        print("  --fast            no tuned profile yet (tool runs with its defaults)")
    bf = gkey_speed.tool_config(spec, "batch_flag", SPEED)
    print(f"  --batch <n>       " + (f"passes `{bf} <n>`" if bf else "not supported by this tool (ignored with a message)"))
    pf = gkey_speed.tool_config(spec, "per_file", SPEED)
    print("  --parallel-files <n>  " + ("runs several input files as concurrent runs through one proxy" if pf
                                        else "not available (tool is not marked one-file-per-run)"))
    print("  --cache / --retries <n>  proxy-side, work with every tool (see `gkey --help`)\n")
    print(_color("1", "Install command"))
    print(f"  {spec.install_cmd}")


def main() -> None:
    usage = (
        "Usage: catalog.py list [--plain] | info <id> | install <id> | hint <id> | "
        "poolsize <default> <explicit|-> <cmd> [args...] | adapt <port> <model> <cmd> [args...]"
    )
    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    action = sys.argv[1]
    if action == "list":
        print_list(plain="--plain" in sys.argv[2:])
    elif action in ("info", "preview"):
        print_preview(sys.argv[2] if len(sys.argv) > 2 else "")
    elif action == "install":
        spec = TOOL_BY_ID.get(sys.argv[2].strip()) if len(sys.argv) > 2 else None
        if not spec:
            sys.stderr.write(f"[gkey] Unknown tool: {sys.argv[2] if len(sys.argv) > 2 else ''}\n")
            sys.exit(1)
        if not install_tool(spec):
            sys.exit(1)
    elif action == "hint":
        spec = TOOL_BY_ID.get(sys.argv[2].strip()) if len(sys.argv) > 2 else None
        if spec:
            print(spec.default_prompt_hint)
    elif action == "poolsize":
        default = int(sys.argv[2])
        explicit = None if sys.argv[3] == "-" else int(sys.argv[3])
        cmd = sys.argv[4] if len(sys.argv) > 4 else ""
        spec = TOOL_BY_ID.get(cmd)
        detected = detect_concurrency(spec.id, sys.argv[5:]) if spec else None
        hint = gkey_speed.speed_pool_hint(spec, sys.argv[5:], SPEED) if spec else None
        print(compute_pool_size(default, explicit, max(detected or 0, hint or 0) or None))
    elif action == "needs-proxy":
        # exit 1 when the tool never calls a model (gkey then skips starting the proxy)
        spec = TOOL_BY_ID.get(sys.argv[2].strip()) if len(sys.argv) > 2 else None
        sys.exit(1 if spec is not None and spec.no_model else 0)
    elif action == "adapt":
        adapt_and_exec(sys.argv[2], sys.argv[3], sys.argv[4:])
    else:
        print(usage)
        sys.exit(1)


if __name__ == "__main__":
    main()
