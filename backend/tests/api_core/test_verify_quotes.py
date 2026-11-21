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
