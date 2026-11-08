"""Pure-function pins: bencode parsing, token maths, route constants."""
import pytest

from app import PRICE_PERIODS
from network import token_from_atomic
from onion_req import _bencode_consume_string


class TestBencodeConsumeString:
    def test_valid_with_remainder(self):
        val, rest = _bencode_consume_string(memoryview(b"5:helloXX"))
        assert val.tobytes() == b"hello"
        assert rest.tobytes() == b"XX"

    def test_empty_string(self):
        val, rest = _bencode_consume_string(memoryview(b"0:rest"))
        assert val.tobytes() == b""
        assert rest.tobytes() == b"rest"

    def test_multidigit_length(self):
        val, rest = _bencode_consume_string(memoryview(b"12:hello  world|END"))
        assert val.tobytes() == b"hello  world"
        assert rest.tobytes() == b"|END"

    @pytest.mark.parametrize("bad", [b"", b"abc", b"5hello", b"99:short"])
    def test_invalid_inputs_raise_valueerror(self, bad):
        with pytest.raises(ValueError):
            _bencode_consume_string(memoryview(bad))


class TestTokenMaths:
    def test_atomic_to_whole_tokens(self):
        assert token_from_atomic(25_000_000 * 10**9) == 25_000_000.0

    def test_zero(self):
        assert token_from_atomic(0) == 0.0


def test_price_periods_are_the_documented_four():
    assert set(PRICE_PERIODS) == {"1h", "1d", "7d", "30d"}
