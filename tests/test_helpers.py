from gkey_common import (
    LOCAL_API_KEY,
    MAX_POOL_SIZE,
    compute_pool_size,
    detect_concurrency,
    mask_secret,
    redact_text,
)


def test_detect_concurrency_forms():
    assert detect_concurrency("pdf2zh", ["a.pdf", "-t", "8"]) == 8
    assert detect_concurrency("pdf2zh", ["a.pdf", "-t8"]) == 8
    assert detect_concurrency("pdf2zh", ["a.pdf", "--thread=6"]) == 6
    assert detect_concurrency("pdf2zh", ["a.pdf", "--thread", "3"]) == 3
    assert detect_concurrency("pdf2zh", ["a.pdf"]) is None
    assert detect_concurrency("pdf2zh", ["-t", "abc"]) is None
    assert detect_concurrency("markitdown", ["-t", "8"]) is None


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
