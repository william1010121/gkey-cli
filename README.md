# gkey

**Run any OpenAI-compatible CLI tool on your Google Antigravity login. No API key, no flags, no config.**

[![npm](https://img.shields.io/npm/v/gkey-cli.svg)](https://www.npmjs.com/package/gkey-cli)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![node](https://img.shields.io/badge/node-%3E%3D18-339933.svg)](package.json)
[![python](https://img.shields.io/badge/python-%3E%3D3.9-3776ab.svg)](requirements.txt)
[![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](#faq)

Most great LLM command-line tools want an OpenAI key and a handful of `--base-url` / `--api-key` / `--model` flags.
`gkey` starts a small local OpenAI-compatible proxy backed by the Antigravity CLI (`agy`) you are already logged in to,
then installs the tool you ask for and fills in every flag and environment variable for it.

```text
$ gkey pdf2zh paper.pdf        # that's the whole command
```

## Quickstart (30 seconds)

Prerequisites: Node 18+, Python 3.9+, and the Antigravity CLI (`agy`) logged in. macOS or Linux (Windows: use WSL2).

```bash
npx gkey-cli                       # pick a tool from the menu
npx gkey-cli pdf2zh paper.pdf      # or run one directly
npm i -g gkey-cli                  # optional: then just `gkey ...`
```

On first run gkey creates a private Python environment in `~/.gkey` (about 10 seconds), then everything is instant.

### The menu

Run `gkey` with no arguments. Type to filter, arrows to move, Enter to pick (uses `fzf` if you have it, a built-in menu if not;
in a pipe or CI it prints a plain numbered list). Picking a tool does not dump documentation on you; it drops you into an
editable prompt with the command prefilled. Add your file and press Enter. Tab completes paths.

```text
$ gkey pdf2zh paper.pdf -p 1-5█
```

Long descriptions live behind `gkey info <tool>`.

### Demo: translating a paper

```text
$ gkey pdf2zh paper.pdf -t 8
[gkey] pdf2zh is not installed; installing it now...        # first time only
[gkey] Proxy ready at http://127.0.0.1:18432/v1             # pool grown to 8 workers to match -t 8
...
paper-mono.pdf              # Traditional Chinese pages only
paper-dual.pdf              # bilingual, side by side
paper-zh-TW-then-en.pdf     # ONE file: all Chinese pages first, then all original pages
```

gkey added `-s openai` and `-lo zh-TW` (Traditional Chinese is the default; pass `-lo ja` etc. to change it), pointed the tool at `http://127.0.0.1:18432/v1`, and supplied a placeholder key. You typed none of it.

### pdf2zh output layout

`gkey pdf2zh paper.pdf` translates to Traditional Chinese (zh-TW) and, by default (`--layout zh-then-en`), also writes
`<name>-zh-TW-then-en.pdf`: the whole translated document followed by the whole original, not interleaved
(a 16-page paper gives 32 pages). Use `--layout dual` to skip that merged file; `-mono.pdf` and `-dual.pdf` are always kept.
With `-p 1-5` only the selected original pages are appended. The merge uses PyMuPDF from pdf2zh's own environment.

pdf2zh (1.9.x) writes some page numbers with exponents (`1.89e-16`), which makes MuPDF-based viewers print
`unknown keyword: '1.89e'` and drop an operator. gkey rewrites those numbers in `-mono.pdf`, `-dual.pdf` and the merged file
after each run. (It is an upstream pdf2zh bug in `pdfinterp.py`; gkey does not patch pdf2zh itself.)

## Supported tools

| Tool | What it does | Example |
| --- | --- | --- |
| `pdf2zh` | Traditional Chinese (zh-TW) paper PDFs; keeps math and layout; Chinese pages then originals | `gkey pdf2zh paper.pdf` |
| `babeldoc` | Next-gen PDF layout translation | `gkey babeldoc paper.pdf` |
| `markitdown` | PDF/PPTX/DOCX/XLSX to Markdown | `gkey markitdown in.pdf -o out.md` |
| `marker_single` | High-accuracy PDF to Markdown (tables, math) | `gkey marker_single paper.pdf` |
| `gpt-subtrans` | Subtitle (.srt/.ass) translation | `gkey gpt-subtrans a.srt -o b.srt` |
| `bbook_maker` | Bilingual EPUB | `gkey bbook_maker book.epub --test` |
| `epub_to_audiobook` | EPUB to audiobook (macOS TTS) | `gkey epub_to_audiobook book.epub out/` |
| `videocaptioner` | Re-segment and translate Whisper subtitles | `gkey videocaptioner` |
| `fabric` | Prompt patterns for pipes | `cat a.md \| gkey fabric -p summarize` |
| `mods` | LLM in UNIX pipes | `git diff \| gkey mods "review"` |
| `ai-renamer` | Rename files by content | `gkey ai-renamer ~/Desktop/shots` |
| `oco` | Commit messages from staged diff | `gkey oco` |
| `gptcommit` | Rust commit message / git hook | `gkey gptcommit` |
| `lobe-i18n` | Translate locale JSON / Markdown | `gkey lobe-i18n` |

`gkey --list` shows install status; `gkey info <tool>` shows details and exactly what gets injected.
Missing tools are installed on first use via `uv`, `npm`, `go`, `brew` or `cargo` as appropriate.

## How it works

```text
 gkey <tool> args ──► adapt flags/env ──► <tool> ──► http://127.0.0.1:18432/v1 ──► agy worker pool ──► Antigravity models
                       (-s openai, base URL,           (OpenAI-compatible proxy,
                        placeholder key, model)         /v1/chat/completions, SSE streaming)
```

1. `gkey` starts (or reuses) a background proxy on `127.0.0.1:18432`, with pid and log under `~/.gkey`.
2. The proxy keeps a pool of warm `agy` workers (stateless, tool-less) and turns OpenAI chat requests into worker turns.
   If `GEMINI_API_KEY` or Vertex credentials are set and the optional `google-antigravity` SDK is installed, it can use that instead.
3. `gkey` rewrites the target tool's arguments and environment so it talks to the proxy, then `exec`s it.

### Worker pool and `-t`

The proxy handles at most `GKEY_POOL_SIZE` (default 4) requests at once. A tool's own thread flag, such as `pdf2zh -t 8`,
only controls how many requests the tool sends. To avoid starving it, gkey reads that flag
(`pdf2zh -t/--thread`, `babeldoc --qps/--pool-max-workers`, `gpt-subtrans --maxthreads`) and grows the pool to match,
restarting the proxy if the running one is smaller. The pool is capped at 16. Override it any time with `--pool-size N`.
Automatic sizing only ever grows the pool; an explicit `--pool-size` is applied exactly.

### Benchmarking the proxy

```bash
gkey --bench                                  # 40 requests, 4 at a time
gkey --bench --concurrency 8 --requests 200 --pool-size 8
gkey --bench-real                             # opt-in: ONE tiny prompt per backend, prints time-to-first-token
```

`--bench` starts a throwaway proxy on a free port with `--fake-backend` (also `GKEY_FAKE_BACKEND=1`; delay via
`GKEY_FAKE_DELAY_MS`, default 20 ms) that answers `/v1/chat/completions` with canned text, streaming and non-streaming.
It never calls a model and leaves your running proxy alone. It prints p50/p95/max latency, requests/s and the effective pool size.

How to read it: the numbers are **proxy overhead** (HTTP, JSON, SSE, pool queueing), not model latency. When concurrency
exceeds the pool size, requests queue, so latency is about `ceil(concurrency / pool) x delay`. Real runs add model time
(seconds), which dominates. To compare backends on real calls, try it yourself with a tiny prompt:
`gkey -b agy mods "reply with OK"` versus `gkey -b sdk mods "reply with OK"`. `--bench-real` does the same, once per backend,
and only when you pass it.

## Configuration

| Variable / flag | Default | Meaning |
| --- | --- | --- |
| `GKEY_HOME` | `~/.gkey` | Venv, pid, logs, workspaces, cloned tools |
| `GKEY_PORT`, `-p` | `18432` | Proxy port |
| `GKEY_HOST` | `127.0.0.1` | Bind address (keep it local, see Security) |
| `GKEY_MODEL`, `-m` | `gemini-3.8-flash-low` | Default model |
| `GKEY_BACKEND`, `-b` | `auto` | `auto`, `sdk` or `agy` |
| `GKEY_EFFORT`, `-e` | `low` | Reasoning effort for `agy` workers |
| `GKEY_POOL_SIZE`, `--pool-size` | `4` | Warm workers / concurrent requests (1-16) |
| `GKEY_TIMEOUT` | `300` | Per-request deadline in seconds |
| `GKEY_START_TIMEOUT` | `60` | Worker start deadline in seconds |
| `GKEY_TARGET_LANG` | `Traditional Chinese` | Target language for tools that need one |
| `GKEY_NO_FZF` | unset | Set to `1` to force the built-in menu |
| `GKEY_INSTALL_SDK` | unset | Set to `1` on first run to also install the optional SDK |

Changing `-m`, `-b`, `-e` or `--pool-size` while a proxy is running restarts it with the new settings.
Other `gkey` processes using the same port will see their connections reset, so use `-p` for parallel setups.

Management: `gkey --status | --start | --stop | --restart | --logs | --env`.

## FAQ

**Do I need an API key?** No. The proxy ignores keys; wrapped tools get the public placeholder `gkey-local-placeholder`.

**Do I need fzf?** No. It is used if present; otherwise the built-in menu runs. Without a TTY you get a numbered list.

**Why is `-t 8` not faster?** The proxy pool used to stay at 4. It now grows automatically (max 16); see the section above.
Rate limits on your Antigravity account still apply.

**A request hangs or fails.** Requests have a 300 s deadline, dead workers surface as errors rather than empty answers, and
`gkey --logs` shows the proxy log. `last_warm_error` in `gkey --status` shows why workers failed to start.

**Windows?** Not supported natively; use WSL2.

**Is this official?** No. gkey is an independent community project, not affiliated with Google or the wrapped tools.
You are responsible for complying with the terms of the services you use through it.

## Security notes

- The proxy has **no authentication** and binds to `127.0.0.1` by default. Do not expose it (a warning is logged if you bind elsewhere).
- gkey **never forwards your real `OPENAI_API_KEY`** to wrapped tools; it always supplies the placeholder.
  Anything key-shaped that gkey prints is masked.
- Prompts are sent to your Antigravity-backed models; do not pipe secrets you would not send there.
- Auto-install runs the tool's documented install command (`uv tool install ...`, `npm install -g ...`). Review `gkey info <tool>` first if unsure.
- Report vulnerabilities privately via GitHub security advisories.

## Contributing

Adding a tool is one `ToolSpec` in `catalog.py` plus a branch in `adapt_args`. Then:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
.venv/bin/python -m pytest -q
node --test tests/launcher.test.js
```

Please keep user-facing text in English, add a test for any new adaptation, and never commit secrets.
GitHub topic suggestions are in [TOPICS.md](TOPICS.md).

## License

MIT, see [LICENSE](LICENSE).
