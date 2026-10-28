#!/usr/bin/env python3
"""Interactive tool picker for `gkey` (no fzf required).

Flow: pick a tool -> get an editable, prefilled prompt `gkey <tool> ` -> append the
file and arguments -> Enter runs it. Uses fzf when available, otherwise a built-in
arrow-key / type-to-filter / number menu, otherwise a plain numbered list.
"""

from __future__ import annotations

import glob
import os
import select
import shlex
import shutil
import subprocess
import sys
from typing import List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import catalog  # noqa: E402

try:  # POSIX only
    import termios
    import tty
except ImportError:  # pragma: no cover
    termios = None  # type: ignore
    tty = None  # type: ignore


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def filter_tools(query: str, tools: Sequence[catalog.ToolSpec]) -> List[catalog.ToolSpec]:
    """Case-insensitive match; every whitespace-separated word must appear somewhere."""
    q = query.strip().lower()
    if not q:
        return list(tools)
    out = []
    for t in tools:
        hay = " ".join([t.id, t.name, t.category, t.summary, *t.aliases]).lower()
        if all(part in hay for part in q.split()):
            out.append(t)
    return out


def resolve_choice(text: str, tools: Sequence[catalog.ToolSpec]) -> Optional[str]:
    """Resolve a 1-based number or a tool id/alias/unique substring typed by the user."""
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        idx = int(text)
        return tools[idx - 1].id if 1 <= idx <= len(tools) else None
    spec = catalog.TOOL_BY_ID.get(text)
    if spec:
        return spec.id
    matches = filter_tools(text, tools)
    return matches[0].id if len(matches) == 1 else None


def numbered_fallback(tools: Sequence[catalog.ToolSpec], out=None) -> None:
    out = out or sys.stdout
    out.write("gkey tools (run `gkey <tool> [args]`, or `gkey info <tool>` for details):\n\n")
    for i, t in enumerate(tools, 1):
        status = "installed" if catalog.is_tool_installed(t) else "not installed"
        out.write(f"{i:>3}. {t.id:<18} {status:<14} {t.summary}\n")
    out.write("\nExample: gkey pdf2zh-next paper.pdf\n")


def pick_with_fzf(tools: Sequence[catalog.ToolSpec]) -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    lines = []
    for t in tools:
        status = "installed" if catalog.is_tool_installed(t) else "not installed"
        lines.append(f"{t.id}\t{t.id:<18} {status:<14} {t.summary}")
    preview = f"{shlex.quote(sys.executable)} {shlex.quote(os.path.join(here, 'catalog.py'))} info {{1}}"
    cmd = [
        "fzf", "--delimiter=\t", "--with-nth=2", "--height=90%", "--layout=reverse",
        "--border=rounded", "--prompt=tool> ",
        "--header=Enter: pick | Esc: quit | type to filter",
        f"--preview={preview}", "--preview-window=right:55%:wrap",
    ]
    res = subprocess.run(cmd, input="\n".join(lines), stdout=subprocess.PIPE, universal_newlines=True)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    return res.stdout.split("\t", 1)[0].strip()


def _read_key(fd: int) -> str:
    ch = os.read(fd, 1)
    if ch == b"\x1b":
        # Arrow keys arrive as ESC [ A/B; a lone ESC means quit.
        if not select.select([fd], [], [], 0.05)[0]:
            return "esc"
        seq = os.read(fd, 2)
        return {b"[A": "up", b"[B": "down", b"OA": "up", b"OB": "down"}.get(seq, "other")
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch in (b"\x7f", b"\x08"):
        return "backspace"
    if ch in (b"\x03", b"\x04"):
        return "quit"
    if ch == b"\x0e":
        return "down"  # ctrl-n
    if ch == b"\x10":
        return "up"  # ctrl-p
    try:
        first = ch[0]
        extra = 3 if first >= 0xF0 else 2 if first >= 0xE0 else 1 if first >= 0xC0 else 0
        if extra:
            ch += os.read(fd, extra)
        return ch.decode("utf-8")
    except Exception:
        return "other"


def pick_with_builtin(tools: Sequence[catalog.ToolSpec]) -> Optional[str]:
    """Arrows / ctrl-n,p move, type to filter, digits+Enter jump to a number, Esc quits."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    query = ""
    cursor = 0
    out = sys.stdout
    shown_lines = 0
    statuses = {t.id: bool(catalog.is_tool_installed(t)) for t in tools}

    def current_matches() -> List[catalog.ToolSpec]:
        return list(tools) if query.isdigit() else filter_tools(query, tools)

    def render(matches: List[catalog.ToolSpec]) -> None:
        nonlocal shown_lines
        size = shutil.get_terminal_size((80, 24))
        width = max(40, size.columns - 1)
        rows = max(3, min(len(tools), size.lines - 4))
        if shown_lines:
            out.write(f"\x1b[{shown_lines}F\x1b[J")
        lines = ["gkey tools - type to filter, arrows to move, Enter to pick, Esc to quit", f"> {query}"]
        top = max(0, min(cursor - rows + 1, len(matches) - rows)) if len(matches) > rows else 0
        for idx, t in enumerate(matches[top:top + rows], top):
            mark = "installed" if statuses[t.id] else "not installed"
            line = f"{idx + 1:>2}. {t.id:<18} {mark:<13} {t.summary}"[: width - 3]
            lines.append(f" > \x1b[7m{line}\x1b[0m" if idx == cursor else f"   {line}")
        if not matches:
            lines.append("   (no match)")
        out.write("\n".join(lines) + "\n")
        out.flush()
        shown_lines = len(lines)

    try:
        tty.setcbreak(fd)
        out.write("\x1b[?25l")
        while True:
            matches = current_matches()
            cursor = max(0, min(cursor, len(matches) - 1))
            render(matches)
            key = _read_key(fd)
            if key in ("esc", "quit"):
                return None
            if key == "enter":
                if query.isdigit():
                    return resolve_choice(query, tools)
                if matches:
                    return matches[cursor].id
            elif key == "up":
                cursor = max(0, cursor - 1)
            elif key == "down":
                cursor = min(len(matches) - 1, cursor + 1)
            elif key == "backspace":
                query = query[:-1]
                cursor = 0
            elif len(key) == 1 and key.isprintable():
                query += key
                cursor = 0
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if shown_lines:
            out.write(f"\x1b[{shown_lines}F\x1b[J")
        out.write("\x1b[?25h")
        out.flush()


def pick_with_prompt(tools: Sequence[catalog.ToolSpec]) -> Optional[str]:
    numbered_fallback(tools)
    try:
        answer = input("\nPick a number or tool name (empty to quit): ")
    except (EOFError, KeyboardInterrupt):
        return None
    return resolve_choice(answer, tools)


def pick_tool(tools: Sequence[catalog.ToolSpec]) -> Optional[str]:
    if os.environ.get("GKEY_NO_FZF") != "1" and shutil.which("fzf"):
        return pick_with_fzf(tools)
    if termios is not None:
        return pick_with_builtin(tools)
    return pick_with_prompt(tools)


def _path_completer(text: str, state: int):
    home = os.path.expanduser("~")
    matches = []
    for p in sorted(glob.glob(os.path.expanduser(text) + "*")):
        shown = ("~" + p[len(home):]) if text.startswith("~") else p
        matches.append(shown + "/" if os.path.isdir(p) else shown)
    return matches[state] if state < len(matches) else None


def _setup_readline(initial: str):
    try:
        import readline
    except ImportError:  # pragma: no cover
        return None
    readline.set_completer_delims(" \t\n")
    readline.set_completer(_path_completer)
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")  # macOS system Python
    else:
        readline.parse_and_bind("tab: complete")
    return readline


def parse_command_line(line: str) -> Optional[List[str]]:
    """Parse the edited line into argv for gkey (dropping a leading `gkey`)."""
    argv = shlex.split(line)
    if argv and argv[0] == "gkey":
        argv = argv[1:]
    return argv or None


def prompt_command(tool_id: str) -> Optional[List[str]]:
    """Show `gkey <tool> ` as editable text (Tab completes paths); return argv."""
    initial = f"gkey {tool_id} "
    rl = _setup_readline(initial)
    try:
        # The command stays on screen as the prompt itself (readline's prefill is
        # unreliable on macOS libedit); the user just appends the arguments.
        line = initial + input(f"$ {initial}")
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\n")
        return None
    try:
        return parse_command_line(line)
    except ValueError as e:
        sys.stderr.write(f"[gkey] Could not parse command: {e}\n")
        return None


def main() -> None:
    tools = catalog.TOOLS
    if not is_interactive():
        numbered_fallback(tools)
        return
    tool_id = pick_tool(tools)
    if not tool_id:
        return
    argv = prompt_command(tool_id)
    if not argv:
        return
    self_script = os.environ.get("GKEY_SELF")
    if not self_script:
        sys.stderr.write("[gkey] GKEY_SELF not set; run: gkey " + " ".join(shlex.quote(a) for a in argv) + "\n")
        return
    os.execv("/usr/bin/env", ["env", "bash", self_script] + argv)


if __name__ == "__main__":
    main()
