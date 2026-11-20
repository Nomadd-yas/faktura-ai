"""normalize_encapsulation (К-7) и normalize_spectrum (spec/06 §6.3). REQ-047."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("value", ["unencapsulated", "Unencapsulated", "non-encapsulated",
                                   "without encapsulation", "not encapsulated", False, "false", "no"])
def test_unencapsulated_false(nz, value):
    assert nz.normalize_encapsulation(value)[0] is False


@pytest.mark.parametrize("value", ["encapsulated", "Encapsulated", True, "true", "yes"])
def test_encapsulated_true(nz, value):
    assert nz.normalize_encapsulation(value)[0] is True


@pytest.mark.parametrize("value", ["epoxy resin and cover glass, 85 C", "sealed with UV resin", "something odd"])
def test_other_text_none_with_note(nz, value):
    """Прочее: булево не угадываем, исходная фраза уходит в пометку."""
    b, note = nz.normalize_encapsulation(value)
    assert b is None
    assert note and value.split()[0] in note


@pytest.mark.parametrize("value", [None, "", "  "])
def test_empty_none(nz, value):
    assert nz.normalize_encapsulation(value)[0] is None


def test_returns_pair(nz):
    r = nz.normalize_encapsulation("encapsulated")
    assert isinstance(r, tuple) and len(r) == 2


# --- spectrum ---
@pytest.mark.parametrize("text", ["AM 1.5G", "AM1.5G", "AM1,5G", "AM 1,5G", "1 sun AM1.5", "AM1.5", "1.5AM",
                                  "AM 1.5 G"])
def test_spectrum_am15g(nz, text):
    assert nz.normalize_spectrum(text) == "AM1.5G"


@pytest.mark.parametrize("text", [None, "", "  "])
def test_spectrum_empty(nz, text):
    assert nz.normalize_spectrum(text) is None


@pytest.mark.parametrize("text", ["white LED", "halogen lamp (non-standard)"])
def test_spectrum_nonstandard_not_forced(nz, text):
    assert nz.normalize_spectrum(text) != "AM1.5G"


def test_spectrum_idempotent(nz):
    assert nz.normalize_spectrum("AM1.5G") == "AM1.5G"
