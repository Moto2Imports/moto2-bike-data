#!/usr/bin/env python3
"""
Tests for photo placeholder-filtering + ordering (filter_and_order_photos).

The network fetch (is_placeholder_photo) is exercised end-to-end by the daily
scrape; here we inject a fake `is_placeholder` predicate so the ordering logic
is deterministic and offline. Fingerprints (2228-byte hero blank, bdsc 404)
were confirmed via a CI probe across multiple live listings.

    python3 test_photo_filter.py
"""
from koscom_scraper_v3 import filter_and_order_photos, is_hero_photo

# 9 hero-grid slots (tru.ru) + 24 bdsc inspection slots, mirroring the real feed.
HERO = [f"https://7.tru.ru/imgs/h{i}" for i in range(9)]
INSP = [f"https://bdsc.jupiter.ac/auctiondata/bds/b{i}.jpg" for i in range(9, 33)]
ALL = HERO + INSP


def order(photos, placeholders):
    ph = set(placeholders)
    return filter_and_order_photos(photos, lambda u: u in ph)


def test_is_hero_photo():
    assert is_hero_photo("https://7.tru.ru/imgs/abc")
    assert is_hero_photo("https://9.ajes.com/imgs/abc")
    assert not is_hero_photo("https://bdsc.jupiter.ac/auctiondata/x.jpg")


def test_full_listing_orders_wholebike_then_inspection_then_accessory():
    # whole-bike 0-5 real, accessory 6 real, 7-8 blank; all inspection real.
    placeholders = [HERO[7], HERO[8]]
    result = order(ALL, placeholders)
    assert result == HERO[:6] + INSP + [HERO[6]]


def test_pre_inspection_shows_only_wholebike():
    # inspection not released (all bdsc are 404 placeholders); 7-9 hero blank.
    placeholders = HERO[6:] + INSP
    result = order(ALL, placeholders)
    assert result == HERO[:6]


def test_real_accessory_goes_to_the_very_end():
    # accessory shots 7-8 are real → after inspection, not interleaved.
    placeholders = [HERO[6]]  # only slot 6 blank; 7,8 real accessory
    result = order(ALL, placeholders)
    assert result == HERO[:6] + INSP + [HERO[7], HERO[8]]
    # every inspection photo precedes every accessory photo
    assert result.index(INSP[-1]) < result.index(HERO[7])


def test_all_placeholders_yields_empty():
    assert order(ALL, ALL) == []


def test_ordering_is_host_based_not_index_based():
    # A short/odd photo set (no fixed 33 layout) still orders correctly.
    photos = [HERO[0], INSP[0], HERO[1], INSP[1]]
    assert order(photos, []) == [HERO[0], HERO[1], INSP[0], INSP[1]]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed")
