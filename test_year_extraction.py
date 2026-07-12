#!/usr/bin/env python3
"""
Unit tests for the Japanese-era year extraction added to koscom_scraper_v3.

Pure-logic tests (era→Gregorian, value/text parsing) plus a few extract_year
cases against synthetic td.bkth spec tables that mirror the sheet structure the
grade parser already relies on.

    python3 test_year_extraction.py

NOTE: these do NOT prove the extractor works against the *real* koscom markup —
the live auction site is not reachable from CI here, so the year-row label and
value format must still be confirmed on a live run (see koscom_scraper_v3.py).
"""
from bs4 import BeautifulSoup

from koscom_scraper_v3 import (
    era_to_gregorian,
    extract_year,
    parse_year_from_text,
    parse_year_from_value,
)


def test_era_to_gregorian():
    assert era_to_gregorian("S", 63) == 1988   # Showa 63
    assert era_to_gregorian("S", 64) == 1989   # Showa 64  ─┐ both 1989: the
    assert era_to_gregorian("H", 1) == 1989    # Heisei 1  ─┘ boundary is moot
    assert era_to_gregorian("H", 7) == 1995
    assert era_to_gregorian("H", 31) == 2019   # Heisei 31 ─┐ both 2019
    assert era_to_gregorian("R", 1) == 2019    # Reiwa 1   ─┘
    assert era_to_gregorian("令和", 2) == 2020
    # implausible era numbers are rejected (guards against false matches)
    assert era_to_gregorian("S", 99) is None
    assert era_to_gregorian("R", 20) is None   # 2038 — future, not a model year


def test_parse_year_from_value():
    assert parse_year_from_value("平成7年") == 1995
    assert parse_year_from_value("平成元年") == 1989      # 元 = year 1
    assert parse_year_from_value("昭和63年") == 1988
    assert parse_year_from_value("令和2年") == 2020
    assert parse_year_from_value("H7") == 1995
    assert parse_year_from_value("S63") == 1988
    assert parse_year_from_value("R2") == 2020
    assert parse_year_from_value("1995") == 1995          # bare Gregorian
    assert parse_year_from_value("H7 (1995/07)") == 1995  # mixed → same year
    assert parse_year_from_value("") is None
    assert parse_year_from_value("N/A") is None


def test_parse_year_from_text_kanji_only():
    # kanji era is trusted anywhere on the page…
    assert parse_year_from_text("... 年式 平成7年 走行 12,477km ...") == 1995
    # …but a bare abbreviation in free text is NOT (collides with grades/VINs)
    assert parse_year_from_text("Grade R  frame MC21-1043217") is None


def _sheet(rows):
    trs = "".join(
        f'<tr><td class="bkth">{lbl}</td><td>{val}</td></tr>' for lbl, val in rows
    )
    return BeautifulSoup(f"<table>{trs}</table>", "html.parser")


def test_extract_year_from_spec_table():
    # English-ish label (koscom renders grade labels in English)
    soup = _sheet([("Year", "H7"), ("engine", "4"), ("frame", "4")])
    assert extract_year(soup, soup.get_text(" ", strip=True)) == 1995

    # Japanese label with kanji era
    soup = _sheet([("年式", "平成7年"), ("general", "4")])
    assert extract_year(soup, soup.get_text(" ", strip=True)) == 1995

    # first-registration label, era abbreviation
    soup = _sheet([("初度登録", "S63"), ("exterior", "3.5")])
    assert extract_year(soup, soup.get_text(" ", strip=True)) == 1988


def test_extract_year_falls_back_to_text():
    # no year row, but a kanji era elsewhere in the page text
    soup = _sheet([("engine", "4"), ("frame", "4")])
    text = "型式 MC21 年式 平成6年 その他"
    assert extract_year(soup, text) == 1994


def test_extract_year_missing_is_none():
    soup = _sheet([("engine", "4"), ("frame", "4")])
    assert extract_year(soup, "no year anywhere here") is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed")
