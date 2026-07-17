#!/usr/bin/env python3
"""
Browse-mode tests: model-name extraction (⚠ selector UNVERIFIED against live
markup — these test the PARSER against the ASSUMED td.bkth structure, not real
koscom HTML) and the end-to-end browse gate in scrape_listing (eligible kept,
too-new dropped, unknown-year dropped, model read off the page).

    python3 test_browse_scrape.py
"""
import types

from bs4 import BeautifulSoup

import koscom_scraper_v3 as sc
from koscom_scraper_v3 import KoscomScraperV3, extract_model_name

sc.REQUEST_DELAY_SECONDS = 0


def _soup(html):
    return BeautifulSoup(html, "html.parser")


# ------------------------------------------------------------ model extraction
def test_model_row_extracted():
    html = '<table><tr><td class="bkth">車名</td><td>NSR250R</td></tr></table>'
    assert extract_model_name(_soup(html)) == "NSR250R"


def test_english_model_label_extracted():
    html = '<table><tr><td class="bkth">Model</td><td>CBR400RR</td></tr></table>'
    assert extract_model_name(_soup(html)) == "CBR400RR"


def test_leading_make_token_stripped():
    html = '<table><tr><td class="bkth">車名</td><td>Honda VFR400R</td></tr></table>'
    assert extract_model_name(_soup(html), make="Honda") == "VFR400R"


def test_no_model_row_returns_none():
    html = '<table><tr><td class="bkth">grade</td><td>4</td></tr></table>'
    assert extract_model_name(_soup(html)) is None


# ---------------------------------------------------------- browse-mode scrape
def _page(model_row=True, era=None):
    rows = ['<tr><td class="bkth">BDS</td><td>Kantou</td></tr>']
    if model_row:
        rows.append('<tr><td class="bkth">車名</td><td>CB400SF</td></tr>')
    if era:
        rows.append(f'<tr><td class="bkth">年式</td><td>{era}</td></tr>')
    return ("<html><body>BDS Kantou 2026-07-22 "
            "<table>" + "".join(rows) + "</table>"
            "Frame No. NC31-1012345 12,000 km 200,000 JPY</body></html>")


def _run_browse(html, cutoff=2000):
    s = KoscomScraperV3()
    s.session = types.SimpleNamespace(get=lambda u, timeout=None:
                                      types.SimpleNamespace(text=html, encoding="utf-8"))
    return s.scrape_listing("https://x/bike-77.htm", "Honda", None,
                            browse_mode=True, cutoff_year=cutoff)


def test_browse_keeps_eligible_and_reads_model():
    b = _run_browse(_page(era="平成7年"))          # Heisei 7 = 1995 <= 2000
    assert b is not None
    assert b["model"] == "CB400SF"                  # read off the page, not config
    assert b["year"] == 1995
    assert b["id"] == "honda-cb400sf-77"


def test_browse_drops_too_new():
    b = _run_browse(_page(era="平成20年"))          # Heisei 20 = 2008 > 2000
    assert b is None


def test_browse_drops_unknown_year():
    b = _run_browse(_page(era=None))                # no year row anywhere
    assert b is None


def test_browse_unknown_model_still_kept_if_year_ok():
    # No model row, but eligible year -> kept as "Unknown" (never silently drop),
    # so a human/the lookup fallback can resolve it later.
    b = _run_browse(_page(model_row=False, era="平成7年"))
    assert b is not None
    assert b["model"] == "Unknown"


def test_browse_boundary_year_at_cutoff_kept():
    # 平成12年 = 2000 == cutoff -> inclusive, kept.
    b = _run_browse(_page(era="平成12年"), cutoff=2000)
    assert b is not None and b["year"] == 2000


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed")
