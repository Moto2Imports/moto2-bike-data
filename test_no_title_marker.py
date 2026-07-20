#!/usr/bin/env python3
"""
Tests for the "no title / registration certificate not included" marker
(koscom's "SHO LOUIS NOT EQUIPPED"): detection + stripping (koscom_common), and
its end-to-end effect in scrape_listing (hasTitle flag + model cleaned), across
both per-model and browse pipelines.

    python3 test_no_title_marker.py
"""
import types

import koscom_scraper_v3 as sc
from koscom_common import has_no_title_marker, strip_no_title_marker

sc.REQUEST_DELAY_SECONDS = 0


# ---------------------------------------------------------------- detection
def test_detects_marker_case_and_space_insensitive():
    assert has_no_title_marker("NSR250R-1 SHO LOUIS NOT EQUIPPED")
    assert has_no_title_marker("... sho louis not equipped ...")
    assert has_no_title_marker("SHO   LOUIS\tNOT  EQUIPPED")


def test_no_false_positive_on_normal_text():
    assert not has_no_title_marker("Engine: — shock: rust; louis vuitton not here")
    assert not has_no_title_marker("CBR250RR")
    assert not has_no_title_marker("")
    assert not has_no_title_marker(None)


# ---------------------------------------------------------------- stripping
def test_strip_removes_marker_and_tidies():
    assert strip_no_title_marker("NSR250R-1 SHO LOUIS NOT EQUIPPED") == ("NSR250R-1", True)
    assert strip_no_title_marker("RGV250-1SP  SHO LOUIS NOT EQUIPPED") == ("RGV250-1SP", True)


def test_strip_noop_when_absent():
    assert strip_no_title_marker("CBR250RR") == ("CBR250RR", False)
    assert strip_no_title_marker("") == ("", False)


# ------------------------------------------------------- scrape_listing wiring
def _detail(model_row, marker=False, bds=True):
    house = "BDS Kantou" if bds else "Yahoo"
    extra = " SHO LOUIS NOT EQUIPPED" if marker else ""
    rows = f'<tr><td class="bkth">車名</td><td>{model_row}{extra}</td></tr>'
    yr = '<tr><td class="bkth">年式</td><td>平成7年</td></tr>'   # 1995, eligible
    return f"<html><body>{house} 2026-07-22 <table>{rows}{yr}</table> Frame MC28-1012345 12,000 km</body></html>"


def _run(html, model=None, browse=False):
    s = sc.KoscomScraperV3()
    s.session = types.SimpleNamespace(get=lambda u, timeout=None:
                                      types.SimpleNamespace(text=html, encoding="utf-8"))
    return s.scrape_listing("https://x/bike-7.htm", "Honda", model,
                            browse_mode=browse, cutoff_year=2000)


def test_permodel_listing_with_marker_flags_no_title():
    # Per-model model comes from config; the marker lives elsewhere on the page.
    b = _run(_detail("NSR250R", marker=True), model="NSR250R-1")
    assert b is not None
    assert b["hasTitle"] is False
    assert b["model"] == "NSR250R-1"          # config label unaffected


def test_permodel_listing_without_marker_has_title():
    b = _run(_detail("NSR250R", marker=False), model="NSR250R-1")
    assert b["hasTitle"] is True


def test_browse_listing_with_marker_flags_and_strips_model():
    # Browse reads the model off the page — the marker must be stripped out of it
    # AND raise the flag.
    b = _run(_detail("CB750F", marker=True), browse=True)
    assert b is not None
    assert b["hasTitle"] is False
    assert b["model"] == "CB750F"             # marker stripped from displayed model


def test_browse_listing_without_marker_has_title():
    b = _run(_detail("CB750F", marker=False), browse=True)
    assert b["hasTitle"] is True
    assert b["model"] == "CB750F"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed")
