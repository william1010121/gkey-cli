import pathlib
import shutil
import subprocess
import sys

import pytest

import catalog
import pdf_post

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_sanitize_content_removes_exponents():
    src = b"q 1 0 0 1 0 0 cm 1.8968225213473183e-16 -2.5E-05 1e+3 0.5 7 cm Q [<00e1>] TJ /F1e1 12 Tf"
    out = pdf_post.sanitize_content(src)
    assert b"e-" not in out.replace(b"/F1e1", b"") and b"1.89e" not in out
    assert b"<00e1>" in out and b"/F1e1" in out  # hex strings and names untouched
    assert b" 0 -0.000025 1000 0.5 7 cm" in out


def test_sanitize_content_leaves_plain_streams_alone():
    src = b"BT /F1 12 Tf 1 0 0 1 72.5 700 Tm (e1 e-2) Tj ET"
    assert pdf_post.sanitize_content(src) == src


def test_parse_pages():
    assert pdf_post.parse_pages("1-3,5", 10) == [0, 1, 2, 4]
    assert pdf_post.parse_pages("8-", 10) == [7, 8, 9]
    assert pdf_post.parse_pages("x", 10) is None
    assert pdf_post.parse_pages("", 10) is None


def _fitz_python():
    """Interpreter that has PyMuPDF: this one, or pdf2zh-next's own tool environment."""
    try:
        import fitz  # noqa: F401
        return sys.executable
    except ImportError:
        return catalog.pdf2zh_python("pdf2zh_next")


def test_merge_zh_then_en_order(tmp_path):
    py = _fitz_python()
    if not py:
        pytest.skip("PyMuPDF not available")
    code = (
        "import sys, fitz, pdf_post\n"
        "d = sys.argv[1]\n"
        "def mk(path, tag, n):\n"
        "    doc = fitz.open()\n"
        "    for i in range(n):\n"
        "        doc.new_page().insert_text((72, 72), f'{tag}{i+1}')\n"
        "    doc.save(path)\n"
        "mk(d + '/mono.pdf', 'ZH', 3); mk(d + '/orig.pdf', 'EN', 3)\n"
        "pdf_post.merge_zh_then_en(d + '/mono.pdf', d + '/orig.pdf', d + '/out.pdf')\n"
        "out = fitz.open(d + '/out.pdf')\n"
        "print(','.join(p.get_text().strip() for p in out))\n"
    )
    res = subprocess.run([py, "-I", "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); exec(sys.stdin.read())", str(tmp_path)],
                         input=code, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ZH1,ZH2,ZH3,EN1,EN2,EN3"


def test_pdf_post_cli_usage():
    res = subprocess.run([sys.executable, str(ROOT / "pdf_post.py")], capture_output=True, text=True)
    assert res.returncode == 2 and "usage" in res.stderr
