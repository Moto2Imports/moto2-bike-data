#!/usr/bin/env python3
"""
Browse-mode tests: model-name extraction (against koscom's CONFIRMED title-div
structure, "MAKE / MODEL" in a div styled color:#8c58a2 + font-size:22px) and
the end-to-end browse gate in scrape_listing (eligible kept, too-new dropped,
unknown-year dropped, model read off the page).

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
# Exact title-div markup captured from a real koscom detail page.
_REAL_TITLE = ("<div style='margin:5px 0px 5px 0px;font-family:Oswald,Arial;"
               "color:#8c58a2;font-size:22px'>HONDA / CBR250RR</div>")


def test_model_from_real_title_div():
    # Regression: the real page structure must yield the MODEL, not the make and
    # not the whole "MAKE / MODEL" string.
    got = extract_model_name(_soup(_REAL_TITLE))
    assert got == "CBR250RR", got
    assert got != "HONDA" and "/" not in got


def test_model_split_takes_model_side():
    html = "<div style='color:#8c58a2;font-size:22px'>SUZUKI / GSX-R400</div>"
    assert extract_model_name(_soup(html)) == "GSX-R400"


def test_model_no_slash_falls_back_to_make_strip():
    html = "<div style='color:#8c58a2;font-size:22px'>Honda VFR400R</div>"
    assert extract_model_name(_soup(html), make="Honda") == "VFR400R"


def test_no_title_div_returns_none():
    # A div without the color+size style is not the title -> None (kept as Unknown).
    html = "<div style='font-size:22px'>not the title</div><table><tr><td>4</td></tr></table>"
    assert extract_model_name(_soup(html)) is None


# ---------------------------------------------------------- browse-mode scrape
def _page(model_row=True, era=None):
    title = ("<div style='font-family:Oswald;color:#8c58a2;font-size:22px'>"
             "Honda / CB400SF</div>") if model_row else ""
    yr = f'<tr><td class="bkth">年式</td><td>{era}</td></tr>' if era else ""
    return ("<html><body>BDS Kantou 2026-07-22 "
            f"{title}<table>{yr}</table>"
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
