#!/usr/bin/env python3
"""
Tests for the "no title / registration certificate not included" marker
(koscom's "SHO LOUIS NOT EQUIPPED"): detection + stripping (koscom_common) and
its effect in scrape_listing (hasTitle flag on every scraped listing).

    python3 test_no_title_marker.py
"""
import types

import koscom_scraper_v3 as sc
from koscom_common import has_no_title_marker, strip_no_title_marker

sc.REQUEST_DELAY_SECONDS = 0


# ---------------------------------------------------------------- detection
def test_detects_marker_case_and_space_insensitive():
    assert has_no_title_marker("CRF250L SHO LOUIS NOT EQUIPPED")
    assert has_no_title_marker("... sho louis not equipped ...")
    assert has_no_title_marker("SHO   LOUIS\tNOT  EQUIPPED")


def test_no_false_positive_on_normal_text():
    assert not has_no_title_marker("Engine: — shock: rust; louis vuitton not here")
    assert not has_no_title_marker("CBR250RR")
    assert not has_no_title_marker("")
    assert not has_no_title_marker(None)


# ---------------------------------------------------------------- stripping
def test_strip_removes_marker_and_tidies():
    assert strip_no_title_marker("CRF250L SHO LOUIS NOT EQUIPPED") == ("CRF250L", True)
    assert strip_no_title_marker("NSR250R-1  SHO LOUIS NOT EQUIPPED") == ("NSR250R-1", True)


def test_strip_noop_when_absent():
    assert strip_no_title_marker("CBR250RR") == ("CBR250RR", False)
    assert strip_no_title_marker("") == ("", False)


# ------------------------------------------------------- scrape_listing wiring
def _detail(marker=False, bds=True):
    house = "BDS Kantou" if bds else "Yahoo"
    extra = " SHO LOUIS NOT EQUIPPED" if marker else ""
    return (f"<html><body>{house} 2026-07-22 "
            f"<table><tr><td class='bkth'>車名</td><td>NSR250R{extra}</td></tr></table>"
            f" Frame MC28-1012345 12,000 km</body></html>")


def _run(html, model="NSR250R-1"):
    s = sc.KoscomScraperV3()
    s.session = types.SimpleNamespace(get=lambda u, timeout=None:
                                      types.SimpleNamespace(text=html, encoding="utf-8"))
    return s.scrape_listing("https://x/bike-7.htm", "Honda", model)


def test_listing_with_marker_flags_no_title():
    b = _run(_detail(marker=True))
    assert b is not None
    assert b["hasTitle"] is False
    assert b["model"] == "NSR250R-1"          # per-model config label unaffected


def test_listing_without_marker_has_title():
    b = _run(_detail(marker=False))
    assert b["hasTitle"] is True


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed")
