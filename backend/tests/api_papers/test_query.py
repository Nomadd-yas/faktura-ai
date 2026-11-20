"""faktura.query: parse_query и to_fact_filter (spec/04 §4.11, spec/13 §13.7, api_contract §4.14).

Чистые функции: внешние сервисы не нужны (нормализатор LocalNormalizer, встроенный профиль solar).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.entities import FactFilter
from faktura.query import ParsedQuery, parse_query, to_fact_filter


def parse(q, normalizer, profile, collection="solar"):
    return parse_query(collection, q, normalizer, profile)


def by_kind(parsed: ParsedQuery, kind: str) -> list[dict]:
    return [f for f in parsed.filters if f["kind"] == kind]


# --- parse_query: число ---------------------------------------------------------------------------

class TestParseNumber:
    def test_pce_above(self, normalizer, profile):
        p = parse("КПД выше 18", normalizer, profile)
        (f,) = by_kind(p, "num")
        assert f["param"] == "pce" and f["op"] == ">" and f["value"] == 18
        assert f["id"] == "num:pce:min"
        assert f["match"] == "КПД выше 18"
        assert f["label"] == "КПД > 18 %"
        assert p.unparsed == ""

    @pytest.mark.parametrize("q, op", [
        ("КПД выше 18", ">"), ("КПД больше 18", ">"), ("КПД более 18", ">"), ("КПД свыше 18", ">"),
        ("КПД > 18", ">"),
        ("КПД от 18", ">="), ("КПД не ниже 18", ">="), ("КПД >= 18", ">="),
        ("КПД ниже 18", "<"), ("КПД меньше 18", "<"), ("КПД менее 18", "<"), ("КПД < 18", "<"),
        ("КПД до 18", "<="), ("КПД не выше 18", "<="), ("КПД <= 18", "<="),
    ])
    def test_operator_words(self, q, op, normalizer, profile):
        (f,) = by_kind(parse(q, normalizer, profile), "num")
        assert f["op"] == op and f["value"] == 18

    def test_decimal_comma(self, normalizer, profile):
        (f,) = by_kind(parse("Voc не ниже 1,05", normalizer, profile), "num")
        assert f["param"] == "voc" and f["op"] == ">=" and f["value"] == pytest.approx(1.05)

    def test_percent_suffix(self, normalizer, profile):
        (f,) = by_kind(parse("КПД > 20 %", normalizer, profile), "num")
        assert f["value"] == 20

    def test_param_synonyms(self, normalizer, profile):
        for text, param in [("pce выше 10", "pce"), ("эффективность выше 10", "pce"),
                            ("фактор заполнения выше 70", "ff"), ("плотность тока выше 20", "jsc"),
                            ("напряжение выше 1", "voc")]:
            nums = by_kind(parse(text, normalizer, profile), "num")
            assert nums and nums[0]["param"] == param, text

    def test_gap_between_param_and_operator_up_to_24_chars(self, normalizer, profile):
        p = parse("КПД образцов в статье выше 18", normalizer, profile)
        assert by_kind(p, "num") and by_kind(p, "num")[0]["param"] == "pce"

    def test_gap_longer_than_24_chars_not_matched(self, normalizer, profile):
        p = parse("КПД " + "очень длинное пояснение без чисел " + "выше 18", normalizer, profile)
        assert not by_kind(p, "num")

    def test_fraction_param_over_one_divided_by_100(self, normalizer, profile):
        """Для параметров-долей число > 1 делится на 100 (FF в профиле solar хранится как %, Recall - в RAG).
        В solar FF задаётся в %, поэтому «FF выше 70» остаётся 70."""
        (f,) = by_kind(parse("FF выше 70", normalizer, profile), "num")
        assert f["value"] == 70

    def test_two_numeric_filters(self, normalizer, profile):
        p = parse("КПД выше 18 Voc ниже 1,2", normalizer, profile)
        assert {f["id"] for f in by_kind(p, "num")} == {"num:pce:min", "num:voc:max"}

    def test_min_and_max_same_param(self, normalizer, profile):
        p = parse("КПД от 15 КПД до 20", normalizer, profile)
        assert {f["id"] for f in by_kind(p, "num")} == {"num:pce:min", "num:pce:max"}


# --- parse_query: год -----------------------------------------------------------------------------

class TestParseYear:
    @pytest.mark.parametrize("q, op, value", [
        ("после 2022", ">", 2022), ("с 2022", ">=", 2022), ("от 2022", ">=", 2022), ("не раньше 2022", ">=", 2022),
        ("до 2023", "<=", 2023), ("раньше 2023", "<=", 2023),
    ])
    def test_year_ops(self, q, op, value, normalizer, profile):
        (f,) = by_kind(parse(q, normalizer, profile), "year")
        assert f["op"] == op and f["value"] == value
        assert f["match"] == q

    def test_year_ids(self, normalizer, profile):
        assert by_kind(parse("после 2022", normalizer, profile), "year")[0]["id"] == "year:min"
        assert by_kind(parse("до 2023", normalizer, profile), "year")[0]["id"] == "year:max"

    def test_year_range(self, normalizer, profile):
        p = parse("с 2021 до 2023", normalizer, profile)
        assert {f["id"] for f in by_kind(p, "year")} == {"year:min", "year:max"}

    def test_year_label_ru(self, normalizer, profile):
        assert by_kind(parse("после 2022", normalizer, profile), "year")[0]["label"] == "после 2022 года"


# --- parse_query: тема, замечания, составные ------------------------------------------------------

class TestParseOther:
    def test_topic(self, normalizer, profile):
        (f,) = by_kind(parse("перовскит", normalizer, profile), "topic")
        assert f["id"] == "topic:перовскит" and f["label"] == "перовскит"

    @pytest.mark.parametrize("word", ["перовскит", "MAPbI", "CIGS", "CdTe", "тандем", "кремний", "олово",
                                      "бессвинцовые", "Sn"])
    def test_topic_words(self, word, normalizer, profile):
        assert by_kind(parse(word, normalizer, profile), "topic"), word

    def test_several_topics_are_separate_filters(self, normalizer, profile):
        p = parse("перовскит CIGS", normalizer, profile)
        assert len(by_kind(p, "topic")) == 2

    @pytest.mark.parametrize("word", ["противоречия", "не сходится", "расхождения", "сомнения", "ошибки"])
    def test_issues_words(self, word, normalizer, profile):
        assert by_kind(parse(word, normalizer, profile), "issues"), word

    def test_combined_spec_example(self, normalizer, profile):
        p = parse("перовскит КПД выше 18 после 2022", normalizer, profile)
        assert [f["kind"] for f in p.filters] == ["topic", "num", "year"]
        assert p.unparsed == ""
        assert p.metric == "pce"

    def test_unparsed_remainder(self, normalizer, profile):
        p = parse("КПД выше 18 абракадабра", normalizer, profile)
        assert by_kind(p, "num")
        assert p.unparsed == "абракадабра"

    def test_all_unparsed(self, normalizer, profile):
        p = parse("абракадабра", normalizer, profile)
        assert p.filters == [] and p.unparsed == "абракадабра"

    def test_empty_and_whitespace(self, normalizer, profile):
        for q in ("", "   "):
            p = parse(q, normalizer, profile)
            assert p.filters == [] and p.unparsed == "" and p.metric is None

    def test_match_is_substring_of_query(self, normalizer, profile):
        q = "перовскит КПД выше 18 после 2022"
        for f in parse(q, normalizer, profile).filters:
            assert f["match"] in q

    def test_case_insensitive(self, normalizer, profile):
        assert by_kind(parse("кпд ВЫШЕ 18", normalizer, profile), "num")

    def test_unknown_collection_is_validation_error(self, normalizer, profile):
        from faktura.domain.errors import AppError

        with pytest.raises(AppError) as e:
            parse("КПД выше 18", normalizer, profile, collection="rag")
        assert e.value.code == "validation_error"


class TestMetric:
    def test_first_named_quality_param(self, normalizer, profile):
        assert parse("Voc выше 1 КПД выше 18", normalizer, profile).metric == "voc"

    def test_default_main_metric_is_pce(self, normalizer, profile):
        assert parse("перовскит", normalizer, profile).metric == "pce"

    def test_metric_none_for_empty(self, normalizer, profile):
        assert parse("", normalizer, profile).metric is None


# --- to_fact_filter -------------------------------------------------------------------------------

class TestToFactFilter:
    def test_returns_fact_filter(self, normalizer, profile):
        ff = to_fact_filter(parse("КПД выше 18", normalizer, profile), {"corpus_id": "crp_x"})
        assert isinstance(ff, FactFilter)
        assert ff.corpus_id == "crp_x"

    def test_empty_parsed_gives_neutral_filter(self, normalizer, profile):
        ff = to_fact_filter(ParsedQuery(), {"collection": "solar"})
        assert ff.collection == "solar"
        assert ff.value_min is None and ff.value_max is None and ff.has_open_issues is None

    def test_issues_kind_sets_has_open_issues(self, normalizer, profile):
        ff = to_fact_filter(parse("противоречия", normalizer, profile), {})
        assert ff.has_open_issues is True

    def test_params_pass_through(self):
        ff = to_fact_filter(ParsedQuery(), {"param": ["pce", "voc"], "paper_id": ["pap_1"], "min": "10",
                                            "max": "25", "review_status": ["new"], "min_confidence": "0.5",
                                            "text": "SrCl2", "has_issues": "true"})
        assert ff.params == ["pce", "voc"]
        assert ff.paper_ids == ["pap_1"]
        assert ff.value_min == Decimal(10) and ff.value_max == Decimal(25)
        assert ff.min_confidence == 0.5
        assert ff.text == "SrCl2"
        assert ff.has_open_issues is True

    def test_by_default_rejected_excluded(self):
        """/facts: по умолчанию без rejected (api_contract §4.8)."""
        ff = to_fact_filter(ParsedQuery(), {})
        assert ff.review_status is not None
        assert "rejected" not in {getattr(s, "value", s) for s in ff.review_status}

    def test_explicit_review_status_all_includes_rejected(self):
        ff = to_fact_filter(ParsedQuery(), {"review_status": ["new", "accepted", "rejected"]})
        assert "rejected" in {getattr(s, "value", s) for s in ff.review_status}

    def test_invalid_number_param_raises_validation_error(self):
        from faktura.domain.errors import AppError

        with pytest.raises(AppError) as e:
            to_fact_filter(ParsedQuery(), {"min": "abc"})
        assert e.value.code == "validation_error"
        assert e.value.field == "min"
