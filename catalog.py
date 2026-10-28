#!/usr/bin/env python3
"""Tool catalog, auto-installer and argument/env adapter for `gkey`."""

from __future__ import annotations

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


TOOLS: list[ToolSpec] = [
    ToolSpec(
        id="pdf2zh",
        name="PDFMathTranslate (pdf2zh)",
        category="PDF / paper translation",
        binary="pdf2zh",
        aliases=["pdfmathtranslate"],
        repo="https://github.com/Byaidu/PDFMathTranslate",
        summary="Translate papers to bilingual PDFs, keeping math, layout and figures intact",
        why_useful="Translates arXiv papers into Traditional Chinese (zh-TW) without wrecking LaTeX formulas, two-column layout or figure placement. Produces a translated-only PDF, a side-by-side bilingual PDF, and (default) one PDF with all Chinese pages first, then all original English pages.",
        install_cmd="uv tool install pdf2zh",
        usage_examples=[
            "gkey pdf2zh paper.pdf                 # Traditional Chinese pages, then the original pages",
            "gkey pdf2zh paper.pdf --layout dual   # keep pdf2zh's own outputs only (no merged PDF)",
            "gkey pdf2zh paper.pdf -lo ja          # other target language (zh-TW is the default)",
            "gkey pdf2zh paper.pdf -p 1-5          # pages 1-5 only",
            "gkey pdf2zh paper.pdf -t 8            # 8 threads (gkey grows its worker pool to match)",
            "gkey -m gemini-4-argon-medium pdf2zh paper.pdf",
        ],
        default_prompt_hint="Append a PDF path, e.g. paper.pdf",
        auto_adapt_note=(
            "Appends `-s openai` (service) and `-lo zh-TW` (target language) unless you pass them. "
            "Outputs in the current directory (or -o): <name>-mono.pdf (translated), <name>-dual.pdf (side by side) and, "
            "with the default `--layout zh-then-en`, <name>-zh-TW-then-en.pdf (all Chinese pages, then all original pages). "
            "`--layout dual` skips the merged PDF. gkey also rewrites pdf2zh's PDFs to fix the MuPDF "
            "`unknown keyword: '1.89e'` error (pdf2zh writes numbers with exponents into the page content)."
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
            "gkey babeldoc --files paper.pdf --lang-out zh-TW",
            "gkey babeldoc paper.pdf --qps 8 --pool-max-workers 24   # gkey grows its worker pool to match (cap 16)",
            "gkey babeldoc paper.pdf --no-auto-extract-glossary      # skip the extra glossary LLM pass (faster)",
        ],
        default_prompt_hint="Append a PDF path, e.g. paper.pdf",
        auto_adapt_note=(
            "Rewrites a positional PDF to `--files` and injects `--openai --openai-model --openai-base-url --openai-api-key`. "
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
            "for many files run several processes yourself (one input per process), e.g. with `xargs -P`."
        ),
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
        install_cmd='uv tool install --with openai "git+https://github.com/machinewrapped/llm-subtrans.git"',
        usage_examples=[
            "gkey gpt-subtrans movie.srt -o movie.zh.srt",
            "gkey gpt-subtrans movie.srt -l 'Japanese' -o movie.ja.srt",
            "gkey gpt-subtrans movie.srt --maxbatchsize 100 --scenethreshold 120 -o movie.zh.srt",
        ],
        default_prompt_hint="Append a subtitle path, e.g. input.srt -o output.srt",
        auto_adapt_note=(
            "Passes `-b <gkey proxy>` (gpt-subtrans always sends its own --apibase default of api.openai.com, which would otherwise beat the "
            "environment), `-k`, `-m <gkey model>` and `-l <GKEY_TARGET_LANG>` (default: Traditional Chinese) unless you give them. "
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
        auto_adapt_note="Creates `~/.config/fabric/.env` (if missing) with the OpenAI-compatible settings, so `fabric --setup` is not needed.",
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
        auto_adapt_note="Writes a `mods.yml` (in ~/.gkey/mods) whose `base-url` points at gkey and sets `MODS_CONFIG`.",
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
        auto_adapt_note="Injects `--provider=openai --api-key=... --base-url=... --model=...`.",
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
            "gkey gptcommit",
            "gkey gptcommit install                          # install the git hook in this repo",
        ],
        default_prompt_hint="Press Enter to run gptcommit",
        auto_adapt_note="Sets `GPTCOMMIT__OPENAI__API_BASE`, `GPTCOMMIT__OPENAI__API_KEY`, `GPTCOMMIT__OPENAI__MODEL`.",
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
        auto_adapt_note="Sets `OPENAI_PROXY_URL` (lobe-i18n's proxy variable) and `OPENAI_API_KEY`.",
    ),
]

# Speed contract kept outside the ToolSpec entries (additive; a ToolSpec attribute of the same
# name wins). Keys per tool id: fast={"flags": [...], "env": {...}, "pool": N, "aliases": {...}},
# batch_flag="--flag", per_file={"value_flags": [...]}. See gkey_speed.py.
SPEED: dict = {
    "pdf2zh": {
        # Upstream default is -t 4 (16 only with --ultrafast, which also changes the pipeline).
        "fast": {"flags": ["-t", "16"], "pool": 16, "aliases": {"-t": ["--thread"]}},
    },
}

TOOL_BY_ID: dict = {}
for _t in TOOLS:
    TOOL_BY_ID[_t.id] = _t
    TOOL_BY_ID[_t.binary] = _t
    for _alias in _t.aliases:
        TOOL_BY_ID[_alias] = _t


def is_tool_installed(spec: ToolSpec) -> Optional[str]:
    ensure_extended_path()
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


def _ensure_mods_config(base_url: str, model: str) -> pathlib.Path:
    """Write a `mods.yml` whose openai base-url points at gkey; return its path."""
    mods_dir = gkey_home() / "mods"
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
    return mods_yml


# ---- pdf2zh: target language, merged output layout, post-processing ----------------------------

PDF2ZH_DEFAULT_LANG_OUT = "zh-TW"  # pdf2zh: SourceHanSerifTW font + "Translate ... to zh-TW" prompt
PDF2ZH_LAYOUTS = ("zh-then-en", "dual")
# pdf2zh options that take a separate value (so the value is not mistaken for an input file).
_PDF2ZH_VALUE_FLAGS = {
    "-p", "--pages", "-li", "--lang-in", "-lo", "--lang-out", "-s", "--service", "-o", "--output",
    "-t", "--thread", "-c", "--vchar", "-f", "--formula", "--prompt", "--config", "--authorized",
}
SPEED["pdf2zh"]["per_file"] = {"value_flags": sorted(_PDF2ZH_VALUE_FLAGS)}


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
    """Pull the bits the post-step needs (inputs, output dir, languages, pages) out of pdf2zh args."""
    info = {"files": [], "output": "", "lang_in": "en", "lang_out": PDF2ZH_DEFAULT_LANG_OUT, "pages": ""}
    keys = {"-o": "output", "--output": "output", "-li": "lang_in", "--lang-in": "lang_in",
            "-lo": "lang_out", "--lang-out": "lang_out", "-p": "pages", "--pages": "pages"}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            if k in keys:
                info[keys[k]] = v
        elif a.startswith("-") and len(a) > 1:
            if a in _PDF2ZH_VALUE_FLAGS:
                if a in keys and i + 1 < len(args):
                    info[keys[a]] = args[i + 1]
                i += 1
        else:
            info["files"].append(a.strip("\"'"))
        i += 1
    return info


def pdf2zh_python(binary: str) -> Optional[str]:
    """Interpreter of pdf2zh's own (uv tool) environment: it has PyMuPDF; gkey's venv does not."""
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


def pdf2zh_postprocess(binary: str, args: List[str], layout: str) -> None:
    """After a successful pdf2zh run: fix MuPDF-breaking numbers, and build the merged PDF."""
    py = pdf2zh_python(binary)
    err = sys.stderr
    if not py:
        err.write("[gkey] Could not locate pdf2zh's Python; skipping the post-step (outputs are the plain pdf2zh files).\n")
        return
    info = parse_pdf2zh_args(args)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_post.py")
    outdir = info["output"] or os.getcwd()
    for f in info["files"]:
        if not f.lower().endswith(".pdf") or not os.path.isfile(f):
            continue
        stem = os.path.splitext(os.path.basename(f))[0]
        mono = os.path.join(outdir, f"{stem}-mono.pdf")
        dual = os.path.join(outdir, f"{stem}-dual.pdf")
        if layout == "zh-then-en" and os.path.isfile(mono):
            merged = os.path.join(outdir, f"{stem}-{info['lang_out']}-then-{info['lang_in']}.pdf")
            cmd = [py, "-I", script, "zh-then-en", mono, f, merged] + ([info["pages"]] if info["pages"] else [])
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                err.write(f"[gkey] Merged PDF ({res.stdout.strip()}): {merged}\n")
            else:
                err.write(f"[gkey] Could not build the merged PDF: {res.stderr.strip()[-300:]}\n")
        existing = [p for p in (mono, dual) if os.path.isfile(p)]
        if existing:
            res = subprocess.run([py, "-I", script, "fix"] + existing, capture_output=True, text=True)
            if res.returncode != 0:
                err.write(f"[gkey] Could not post-fix pdf2zh outputs: {res.stderr.strip()[-300:]}\n")


def run_pdf2zh(binary: str, args: List[str], layout: str) -> int:
    """Run pdf2zh as a child (not exec) so the post-step can follow."""
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

    if spec.id == "pdf2zh":
        args, layout = split_pdf2zh_layout(args)
        env["GKEY_PDF2ZH_LAYOUT"] = layout
        if not any(a in ("-s", "--service") or a.startswith("-s=") or a.startswith("--service=") for a in args):
            args.extend(["-s", "openai"])
        if not any(a == "-lo" or a.startswith("-lo=") or a == "--lang-out" or a.startswith("--lang-out=") for a in args):
            args.extend(["-lo", PDF2ZH_DEFAULT_LANG_OUT])

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
            args.extend(["-m", model])
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
        env["DEFAULT_VENDOR"] = "OpenAI"
        env["DEFAULT_MODEL"] = model

    elif spec.id == "mods":
        env["MODS_CONFIG"] = str(_ensure_mods_config(base_url, model))

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
        env["OCO_AI_PROVIDER"] = "openai"
        env["OCO_API_KEY"] = api_key
        env["OCO_API_URL"] = base_url
        env["OCO_MODEL"] = "gpt-4o-mini"

    elif spec.id == "gptcommit":
        env["GPTCOMMIT__OPENAI__API_KEY"] = api_key
        env["GPTCOMMIT__OPENAI__API_BASE"] = base_url
        env["GPTCOMMIT__OPENAI__MODEL"] = "gpt-4o-mini"

    elif spec.id == "lobe-i18n":
        env["OPENAI_PROXY_URL"] = base_url
        env["OPENAI_API_KEY"] = api_key

    return args


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
    post = (lambda a: pdf2zh_postprocess(binary, a, layout)) if spec.id == "pdf2zh" and layout else None
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
        cmd_name = spec.binary

        # Help/version flags pass through untouched
        is_help = any(a in ("-h", "--help", "-v", "--version") for a in args)
        if not is_help:
            args = adapt_args(spec, args, port, model, env_updates)
            args = apply_speed_options(spec, args, env_updates)

    layout = env_updates.pop("GKEY_PDF2ZH_LAYOUT", None)
    os.environ.update(env_updates)
    if spec is not None and not is_help_run(argv[1:]):
        rc = maybe_run_parallel(spec, cmd_name, args, layout)
        if rc is not None:
            sys.exit(rc)
    if spec is not None and spec.id == "pdf2zh" and layout:
        sys.exit(run_pdf2zh(cmd_name, args, layout))
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
    elif action == "adapt":
        adapt_and_exec(sys.argv[2], sys.argv[3], sys.argv[4:])
    else:
        print(usage)
        sys.exit(1)


if __name__ == "__main__":
    main()
