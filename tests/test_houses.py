import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from common.houses import classify, natural_sort_key, tokenize
from common.normalize import canonicalize_street, get_street_exclude_reason


@pytest.mark.parametrize(
    "raw,expected",
    [
        (" 1, 11, 14, 1а, 21 а", ["1", "11", "14", "1а", "21 а"]),
        ("10,бл.14, 11", ["10,бл.14", "11"]),
        ("17/4,б.4, 17/5", ["17/4,б.4", "17/5"]),
        ("45 бокс 2, 45/10", ["45 бокс 2", "45/10"]),
        ("оп.2, оп.12, 5", ["оп.2", "оп.12", "5"]),
        (10, ["10"]),
        ("", []),
        (None, []),
    ],
)
def test_tokenize(raw, expected):
    assert tokenize(raw) == expected


@pytest.mark.parametrize(
    "token,expected_class,expected_base,expected_route",
    [
        ("21", "house", "21", "clean"),
        ("21а", "house", "21а", "clean"),
        ("21 а", "house", "21а", "clean"),
        ("21-А", "house", "21а", "clean"),
        ("21А", "house", "21а", "clean"),
        ("18 А", "house", "18а", "clean"),
        ("1/144", "house_frac", "1/144", "clean"),
        ("11/A", "house", "11а", "clean"),
        ("20/а", "house", "20а", "clean"),
        ("21/А", "house", "21а", "clean"),
        ("2/12-А", "house_frac", "2/12а", "clean"),
        ("13/Б", "house", "13б", "clean"),
        ("1/155 б.2", "block", "1/155", "clean"),
        ("28 блок 62", "block", "28", "clean"),
        ("15 бл.2/4", "block", "15", "clean"),
        ("10,бл.14", "block", "10", "clean"),
        ("19 бокс 3", "box", "19", "clean"),
        ("9 бокс1", "box", "9", "clean"),
        ("9/59 бокс", "box", "9/59", "clean"),
        ("4гараж", "garage", "4", "clean"),
        ("11гар", "garage", "11", "clean"),
        ("10 гараж", "garage", "10", "clean"),
        ("20/6гараж", "garage", "20/6", "clean"),
        ("28 кв.2", "apartment", "28", "clean"),
        ("13 к.12", "apartment", "13", "clean"),
        ("3 пр. 1", "apartment", "3", "clean"),
        ("5/6 під.1", "apartment", "5/6", "clean"),
        ("31 (1)", "note", "31", "clean"),
        ("1 (костел)", "note", "1", "clean"),
        ("145 (оп.5)", "pole", None, "excluded"),
        ("оп.52", "pole", None, "excluded"),
        ("оп.6а", "pole", None, "excluded"),
        ("оп.2/21", "pole", None, "excluded"),
        ("145км.", "road_km", None, "excluded"),
        ("302км+275м", "road_km", None, "excluded"),
        ("140+255", "road_km", None, "excluded"),
        ("ПК 0+57,89 - ПК 4+80", "road_km", None, "excluded"),
        ("1 км", "road_km", None, "excluded"),
        ("45-9", "suspicious_range", None, "review"),
        ("58-925", "suspicious_range", None, "review"),
        ("квад.7буд2", "other", None, "review"),
    ],
)
def test_classify(token, expected_class, expected_base, expected_route):
    res = classify(token, house_fixes={})
    assert res["class"] == expected_class
    assert res["base"] == expected_base
    assert res["route"] == expected_route


def test_classify_with_house_fix():
    res = classify(
        "45-9",
        settlement="Старокостянтинів",
        street="вул. Грушевського",
        house_fixes={"Старокостянтинів|вул. Грушевського|45-9": "45/9"},
    )
    assert res["class"] == "house_frac"
    assert res["base"] == "45/9"
    assert res["route"] == "clean"


def test_natural_sort():
    houses = ["10", "2", "1", "2а", "11", "2/12а", "2/2"]
    assert sorted(houses, key=natural_sort_key) == ["1", "2", "2/2", "2/12а", "2а", "10", "11"]


@pytest.mark.parametrize(
    "settlement,raw,expected",
    [
        ("Старокостянтинів", "вул. Чапаева", "вул. Ринкова"),
        ("Старокостянтинів", "вул. Ш.Алейхема", "вул. Шолом-Алейхема"),
        ("Ланок", "вул. Карєрна", "вул. Кар'єрна"),
        ("Старокостянтинів", "вул. М.Грушевського", "вул. М. Грушевського"),
        ("Старокостянтинів", "пров. Польовий 1", "пров. 1-й Польовий"),
        ("Старокостянтинів", "вул. 20 дистанції колії", "вул. 20 дистанції колії"),
    ],
)
def test_street_canonicalization(settlement, raw, expected):
    canon, _ = canonicalize_street(raw, settlement)
    assert canon == expected


def test_street_exclude_word_boundary():
    assert get_street_exclude_reason("вул. Складська", "Старокостянтинів") is None
    assert (
        get_street_exclude_reason(
            "вул. територія Веснянського старостинського округу Старокостянтинівської міської територіальної громади",
            "Веснянка",
        )
        == "manual"
    )
