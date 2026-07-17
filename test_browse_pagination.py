#!/usr/bin/env python3
"""
Structural tests for the make-browse pagination + dry-run counter — no live
access needed. Exercises: per-page dedup (a bike has several anchors),
multi-page walking, correct termination (empty page AND last-page re-serve),
and the exclude_seen / Phase-A-claims-first ordering rule.

    python3 test_browse_pagination.py
"""
import types
from urllib.parse import urlparse, parse_qs

import koscom_scraper_v3 as sc
from koscom_scraper_v3 import KoscomScraperV3

sc.REQUEST_DELAY_SECONDS = 0        # no real sleeping in tests


def _card(lid):
    """A single bike as the site renders it: thumbnail + title + price anchors,
    all pointing at the same /bike-<id>.htm — must collapse to one listing."""
    return (f'<a href="/bike-{lid}.htm"><img src="x"></a>'
            f'<a href="/bike-{lid}.htm">title {lid}</a>'
            f'<a href="/bike-{lid}.htm">¥price</a>')


def _session(pages):
    """pages: {page_number: [listing_ids]}. Missing page -> empty results."""
    def get(url, timeout=None):
        page = int(parse_qs(urlparse(url).query)["page"][0])
        ids = pages.get(page, [])
        html = "<html><body>" + "".join(_card(i) for i in ids) + "</body></html>"
        return types.SimpleNamespace(text=html, encoding="utf-8")
    return types.SimpleNamespace(get=get)


def _scraper(pages):
    s = KoscomScraperV3()
    s.session = _session(pages)
    s.bikes = []
    s.seen_listing_ids = set()
    return s


def test_counter_dedupes_multi_anchor_and_walks_pages():
    s = _scraper({1: ["1001", "1002", "1003"], 2: ["1004", "1005"], 3: []})
    assert s.count_make_listings("Honda", 2000) == 5


def test_counter_terminates_on_last_page_reserve():
    # koscom sometimes re-serves the final page past the end instead of an empty
    # page; termination must key on "no new ids", not "non-empty page".
    s = _scraper({1: ["1001", "1002"], 2: ["1003"], 3: ["1003"], 4: ["1003"]})
    assert s.count_make_listings("Yamaha", 2000) == 3


def test_counter_is_independent_of_seen_state():
    # The counter measures raw make population, ignoring Phase-A claims.
    s = _scraper({1: ["1001", "1002"], 2: []})
    s.seen_listing_ids.update({"1001", "1002"})
    assert s.count_make_listings("Suzuki", 2000) == 2


def test_browse_urls_skip_phase_a_claimed_lots():
    # exclude_seen: a lot already claimed by Phase A is not re-yielded, but
    # pagination still reaches later pages (no early stop).
    s = _scraper({1: ["1001", "1002", "1003"], 2: ["1004"], 3: []})
    s.seen_listing_ids.update({"1002"})           # Phase A already took 1002
    urls = s.browse_make_urls("Honda", 2000)
    ids = [u.split("bike-")[1].split(".htm")[0] for u in urls]
    assert ids == ["1001", "1003", "1004"], ids
    # everything walked is now marked seen (claimed) — including the skipped one
    assert {"1001", "1002", "1003", "1004"} <= s.seen_listing_ids


def test_per_model_search_claims_and_returns_unique():
    s = _scraper({1: ["2001", "2001", "2002"], 2: []})   # dup anchors across page
    urls = s.search_listing_urls("Honda", "CBR250RR")
    assert len(urls) == 2
    assert s.seen_listing_ids == {"2001", "2002"}


def test_two_makes_share_dedup_across_the_run():
    # A lot surfacing under two makes/searches in one run is claimed once.
    s = _scraper({1: ["3001", "3002"], 2: []})
    first = s.browse_make_urls("Honda", 2000)
    second = s.browse_make_urls("Honda", 2000)             # same lots, already claimed
    assert len(first) == 2 and len(second) == 0


def _capture_urls(pages):
    """A scraper whose session records every requested URL (still serving pages)."""
    s = _scraper(pages)
    base_get = s.session.get
    urls = []

    def rec(url, timeout=None):
        urls.append(url)
        return base_get(url, timeout=timeout)

    s.session = types.SimpleNamespace(get=rec)
    return s, urls


def test_browse_query_carries_force_and_max_year():
    # The confirmed koscom make-browse query: manuf + max_year + force=1.
    s, urls = _capture_urls({1: ["1001"], 2: []})
    s.browse_make_urls("Honda", 2000)
    assert urls, "no request made"
    assert all("manuf=Honda" in u and "max_year=2000" in u and "force=1" in u
               for u in urls), urls


def test_counter_query_carries_force_and_max_year():
    s, urls = _capture_urls({1: ["1001"], 2: []})
    s.count_make_listings("Kawasaki", 2004)
    assert all("manuf=Kawasaki" in u and "max_year=2004" in u and "force=1" in u
               for u in urls), urls


def test_per_model_query_has_no_force_or_max_year():
    # Per-model search is unchanged: model present, no force/max_year.
    s, urls = _capture_urls({1: ["2001"], 2: []})
    s.search_listing_urls("Honda", "CBR250RR")
    assert any("model=CBR250RR" in u for u in urls), urls
    assert all("force=" not in u and "max_year=" not in u for u in urls), urls


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed")
