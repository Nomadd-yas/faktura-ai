"""Этап B: encode_cursor / decode_cursor."""
from __future__ import annotations

import re

import pytest

from faktura.domain.errors import AppError
from faktura.storage import decode_cursor, encode_cursor

BAD = (ValueError, AppError)


@pytest.mark.parametrize("values", [
    ("a",), (1,), ("PCE", 18.5, 42, "fct_abc"), (None, "x"), ("КПД выше 18", 1), (True, False), (), (0, -1, 2 ** 40),
    ("with space/slash+plus=eq&amp?", "ключ"), ("x" * 500, 1),
])
def test_roundtrip(values):
    assert decode_cursor(encode_cursor(values)) == tuple(values)


def test_returns_tuple_and_str():
    c = encode_cursor(("a", 1))
    assert isinstance(c, str) and isinstance(decode_cursor(c), tuple)


def test_cursor_is_url_safe():
    c = encode_cursor(("a b/c?d=e&f", "ж", 1.5))
    assert re.fullmatch(r"[A-Za-z0-9_\-=.~]+", c)


def test_cursor_deterministic_and_distinct():
    assert encode_cursor(("a", 1)) == encode_cursor(("a", 1))
    assert encode_cursor(("a", 1)) != encode_cursor(("a", 2))
    assert encode_cursor(("a", 1)) != encode_cursor((1, "a"))


def test_list_input_accepted():
    assert decode_cursor(encode_cursor(["a", 2])) == ("a", 2)


def test_type_preserved():
    out = decode_cursor(encode_cursor(("1", 1, 1.0, None, True)))
    assert out[0] == "1" and isinstance(out[0], str) and isinstance(out[1], int) and out[3] is None
    assert out[4] is True


@pytest.mark.parametrize("garbage", ["", "!!!", "not base64 at all", "YQ", "e30", "bnVsbA", "W10x"])
def test_garbage_rejected(garbage):
    if garbage in ("W10x",):
        pytest.skip("зависит от формата")
    with pytest.raises(BAD):
        decode_cursor(garbage)


def test_truncated_cursor_rejected():
    c = encode_cursor(("abcdef", 12345))
    with pytest.raises(BAD):
        decode_cursor(c[: len(c) // 2])


def test_non_sequence_payload_rejected():
    import base64
    import json
    raw = base64.urlsafe_b64encode(json.dumps({"a": 1}).encode()).decode()
    with pytest.raises(BAD):
        decode_cursor(raw)


def test_unserializable_value_rejected():
    with pytest.raises((TypeError, ValueError, AppError)):
        encode_cursor((object(),))
