from gkey_common import (
    LOCAL_API_KEY,
    MAX_POOL_SIZE,
    compute_pool_size,
    detect_concurrency,
    mask_secret,
    redact_text,
)


def test_detect_concurrency_forms():
    assert detect_concurrency("pdf2zh-next", ["a.pdf", "--qps", "8"]) == 8
    assert detect_concurrency("pdf2zh-next", ["a.pdf", "--pool-max-workers=6"]) == 6
    assert detect_concurrency("pdf2zh-next", ["a.pdf", "--qps", "3", "--pool-max-workers", "12"]) == 12
    assert detect_concurrency("pdf2zh-next", ["a.pdf"]) == 16  # gkey injects 16 when neither is given
    assert detect_concurrency("pdf2zh-next", ["--qps", "abc"]) is None
    assert detect_concurrency("gpt-subtrans", ["a.srt", "--maxthreads", "5"]) == 5
    assert detect_concurrency("babeldoc", ["a.pdf"]) is None
    assert detect_concurrency("markitdown", ["-t", "8"]) is None
    assert detect_concurrency("babeldoc", ["a.pdf", "-q", "12"]) == 12
    assert detect_concurrency("babeldoc", ["a.pdf", "--qps", "8", "--pool-max-workers", "24"]) == 24
    assert detect_concurrency("marker_single", ["a.pdf", "--max_concurrency", "8"]) == 8
    assert detect_concurrency("bbook_maker", ["b.epub", "--parallel-workers=4"]) == 4
    assert detect_concurrency("epub_to_audiobook", ["b.epub", "o", "--worker_count", "4"]) == 4
    assert detect_concurrency("videocaptioner", ["subtitle", "a.srt", "--thread-num", "8"]) == 8
    assert detect_concurrency("gpt-subtrans", ["a.srt", "--maxthreads", "8"]) is None


def test_pool_sizing():
    assert compute_pool_size(4) == 4
    assert compute_pool_size(4, detected=8) == 8
    assert compute_pool_size(4, detected=2) == 4  # never below the default
    assert compute_pool_size(4, detected=100) == MAX_POOL_SIZE == 16
    assert compute_pool_size(4, explicit=2, detected=8) == 2  # explicit wins
    assert compute_pool_size(4, explicit=99) == 16
    assert compute_pool_size(4, explicit=0) == 1


def test_mask_secret():
    real = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    masked = mask_secret(real)
    assert real not in masked and masked.startswith("sk-") and "..." in masked
    assert mask_secret("short") == "*****"
    assert mask_secret("") == "(empty)"
    assert mask_secret(LOCAL_API_KEY) == LOCAL_API_KEY


def test_redact_text():
    out = redact_text("key=sk-abcdefghijklmnop1234 and AIzaSyA1234567890abcdefghijk")
    assert "sk-abcdefghijklmnop1234" not in out
    assert "AIzaSyA1234567890abcdefghijk" not in out


def test_placeholder_is_not_secret_shaped():
    assert not LOCAL_API_KEY.startswith(("sk-", "AIza"))
