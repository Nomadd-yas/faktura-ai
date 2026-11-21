"""Цитаты в маркерах [[page:…|цитата]] / [[chunk:…|цитата]]: модель берёт их в кавычки, текст PDF - с переносами."""
from faktura.agent.verify import clean_quote, quote_in

PAGE = "the distribution gap between user queries and know-\nledge bases in a specific domain. We ﬁnd that QA pairs…"


def test_quote_matching_tolerates_wrapping_quotes_hyphenation_and_typography():
    assert quote_in('"the distribution gap between user queries and knowledge bases in a specific domain"', PAGE)
    assert quote_in("«We find that QA pairs…»", PAGE)
    assert quote_in("“the  Distribution gap”.", PAGE)
    assert not quote_in('"QA pairs are more closely aligned with the query intent"', PAGE)
    assert not quote_in('""', PAGE) and not quote_in("ab", PAGE)
    assert clean_quote(' "x y" ') == "x y" and clean_quote("«цитата…»") == "цитата" and clean_quote(None) is None


def test_dropped_list_items_leave_no_bare_numbers_and_list_is_renumbered():
    from faktura.agent.verify import BARE_LIST_ITEM, _renumber

    assert BARE_LIST_ITEM.fullmatch("1.") and BARE_LIST_ITEM.fullmatch(" - ") and not BARE_LIST_ITEM.fullmatch("1. текст")
    assert _renumber(["Итог:", "2. второй", "4. четвёртый", "Текст", "3. новый список"]) == \
        ["Итог:", "1. второй", "2. четвёртый", "Текст", "1. новый список"]


def test_pdf_ligature_glyph_in_text_layer_matches_plain_letters():
    page = "exhibited a drop of its initial PCE by 50% a\ue09der 35 h (T50) and lost"
    assert quote_in("a drop of its initial PCE by 50% after 35 h (T50)", page)
    assert not quote_in("a drop of its initial PCE by 50% before 35 h", page)
