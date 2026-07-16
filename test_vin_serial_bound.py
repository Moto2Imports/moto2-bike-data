#!/usr/bin/env python3
"""
Tests for the VIN serial UPPER-BOUND check (evaluate_vin_serial_bound).

This backs the Honda HORNET250 rule: HORNET250 is entirely MC31, but only
units below MC31-1250000 clear the 25-year import line. The bound is a SOFT
include (serial ceiling), never a prefix filter — so the three verdicts are:

    keep  — a matching-prefix serial was found and is below the bound
    skip  — a matching-prefix serial was found at/above the bound (too new)
    flag  — no matching-prefix serial found; the scraper KEEPS the listing but
            logs a WARN for manual review (never silently drop a real listing)

    python3 test_vin_serial_bound.py
"""
from koscom_scraper_v3 import evaluate_vin_serial_bound

PREFIX, MAX = "MC31", 1250000


def test_below_bound_kept_and_vin_pinned():
    text = "Frame No. MC31-1249999 grade 4 mileage 18,220 km"
    assert evaluate_vin_serial_bound(text, PREFIX, MAX) == ("keep", "MC31-1249999")


def test_well_below_bound_kept():
    text = "車台番号 MC31-1001234 ..."
    assert evaluate_vin_serial_bound(text, PREFIX, MAX) == ("keep", "MC31-1001234")


def test_at_bound_is_too_new_skipped():
    # Boundary is exclusive: exactly 1250000 is NOT below the bound -> skip.
    text = "Frame MC31-1250000 here"
    assert evaluate_vin_serial_bound(text, PREFIX, MAX) == ("skip", None)


def test_above_bound_skipped():
    text = "Frame MC31-1300500 newer bike"
    assert evaluate_vin_serial_bound(text, PREFIX, MAX) == ("skip", None)


def test_no_matching_prefix_serial_is_flagged_not_dropped():
    # A HORNET250 page whose frame number didn't extract (OCR/markup miss) must
    # be KEPT (verdict "flag"), never silently dropped.
    text = "Honda Hornet 250 grade 4 mileage 22,010 km lot 2038850534"
    assert evaluate_vin_serial_bound(text, PREFIX, MAX) == ("flag", None)


def test_wrong_prefix_present_still_flags():
    # Some other frame code on the page must not satisfy the MC31 bound.
    text = "Frame MC22-1105000 (a CBR, not a Hornet)"
    assert evaluate_vin_serial_bound(text, PREFIX, MAX) == ("flag", None)


def test_prefix_embedded_in_full_page_text():
    text = ("BDS Kantou · auction 2026-07-22 · 車台番号 MC31-1180777 · "
            "grade 4.5 · 12,900 km · ¥250,000")
    assert evaluate_vin_serial_bound(text, PREFIX, MAX) == ("keep", "MC31-1180777")


def test_generalizes_to_other_prefix_and_bound():
    # The helper is not HORNET-specific; any {prefix, max} works.
    assert evaluate_vin_serial_bound("Frame ZZ99-000500 x", "ZZ99", 1000) == ("keep", "ZZ99-000500")
    assert evaluate_vin_serial_bound("Frame ZZ99-002000 x", "ZZ99", 1000) == ("skip", None)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed")
