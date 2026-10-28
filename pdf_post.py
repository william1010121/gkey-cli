#!/usr/bin/env python3
"""pdf2zh-next post-processing for gkey.

Two jobs, both done on pdf2zh's *outputs* (nothing inside pdf2zh is patched):

1. ``sanitize_content``: pdf2zh 1.9.x (pdfinterp.py; defensive for pdf2zh-next, whose BabelDOC writer may not need it) writes the inverse CTM into form
   XObject streams with Python's ``repr(float)``, which can give scientific notation such as
   ``1.8968225213473183e-16``. PDF content streams do not allow exponents, so MuPDF prints
   ``syntax error: unknown keyword: '1.8968225213473183e'`` and drops that operator. We
   rewrite those numbers in fixed notation.
2. ``merge``: build ONE PDF with all translated pages first, then all original pages.

The PDF work needs PyMuPDF, which lives in pdf2zh-next's own tool environment, so the gkey
launcher runs this file with that interpreter:  python pdf_post.py zh-then-en MONO ORIG OUT [PAGES]  |  python pdf_post.py fix FILE...
"""

from __future__ import annotations

import re
import sys
from typing import List, Optional

# A number with an exponent as a whole whitespace/bracket-delimited token.
_SCI_RE = re.compile(rb"(?<=[\s\[])([-+]?(?:\d+\.?\d*|\.\d+)[eE][-+]?\d+)(?=[\s\]])")


def _fixed(m: "re.Match[bytes]") -> bytes:
    text = ("%.6f" % float(m.group(1))).rstrip("0").rstrip(".")
    if text in ("", "-0", "+0"):
        text = "0"
    return text.encode("ascii")


def sanitize_content(data: bytes) -> bytes:
    """Rewrite scientific-notation numbers in a PDF content stream as fixed notation."""
    return _SCI_RE.sub(_fixed, data)


def parse_pages(spec: Optional[str], total: int) -> Optional[List[int]]:
    """Parse the --pages value ("1-5,8", "3-") into 0-based indexes; None if unusable."""
    if not spec:
        return None
    out: List[int] = []
    try:
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                lo = int(a) if a else 1
                hi = int(b) if b else total
                out.extend(range(lo - 1, hi))
            else:
                out.append(int(part) - 1)
    except ValueError:
        return None
    out = sorted({p for p in out if 0 <= p < total})
    return out or None


def sanitize_document(doc) -> int:
    """Fix scientific-notation numbers in page and form-XObject streams; return #streams changed."""
    page_streams = set()
    for page in doc:
        page_streams.update(page.get_contents())
    changed = 0
    for xref in range(1, doc.xref_length()):
        try:
            if not doc.xref_is_stream(xref):
                continue
            is_form = doc.xref_get_key(xref, "Subtype") == ("name", "/Form")
            if xref not in page_streams and not is_form:
                continue
            raw = doc.xref_stream(xref)
        except Exception:
            continue
        if raw and _SCI_RE.search(raw):
            doc.update_stream(xref, sanitize_content(raw))
            changed += 1
    return changed


def merge_zh_then_en(mono_path: str, orig_path: str, out_path: str, pages: Optional[str] = None) -> str:
    """Write OUT = [all pages of MONO] + [matching pages of ORIG]. Returns a status line."""
    import fitz  # PyMuPDF (pdf2zh-next dependency)

    mono = fitz.open(mono_path)
    orig = fitz.open(orig_path)
    fixed = sanitize_document(mono)

    if mono.page_count != orig.page_count:
        selected = parse_pages(pages, orig.page_count)
        if selected is not None and len(selected) == mono.page_count:
            orig.select(selected)
        # otherwise keep all original pages
    out = fitz.open()
    out.insert_pdf(mono)
    out.insert_pdf(orig)
    out.save(out_path, garbage=3, deflate=True)
    return f"{mono.page_count} translated + {orig.page_count} original = {out.page_count} pages; fixed {fixed} stream(s)"


def fix_file(path: str) -> int:
    """Sanitize a PDF in place (only rewritten when something needed fixing)."""
    import os

    import fitz

    doc = fitz.open(path)
    changed = sanitize_document(doc)
    if changed:
        tmp = path + ".gkey-tmp"
        doc.save(tmp, garbage=3, deflate=True)
        doc.close()
        os.replace(tmp, path)
    return changed


def main(argv: List[str]) -> int:
    if len(argv) >= 3 and argv[1] == "fix":
        for f in argv[2:]:
            print(f"fixed {fix_file(f)} stream(s) in {f}")
        return 0
    if len(argv) >= 5 and argv[1] == "zh-then-en":
        print(merge_zh_then_en(argv[2], argv[3], argv[4], argv[5] if len(argv) > 5 else None))
        return 0
    sys.stderr.write("usage: pdf_post.py fix FILE... | zh-then-en MONO ORIG OUT [PAGES]\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
