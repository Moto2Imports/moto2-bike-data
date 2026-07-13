#!/usr/bin/env python3
"""
Tests for the generic VIN/frame-number extraction (GENERIC_VIN_RE).

The old pattern required a letter-leading prefix and silently missed every
Yamaha (whose model codes are digit-leading: 3XV, 4L3, 2KR…). These tests pin
the broadened pattern against REAL codes from all four makers (so nothing that
parsed before regresses) plus negative cases (dates, lot numbers, model
strings) so the wider prefix doesn't start false-matching non-VIN page text.

    python3 test_vin_extraction.py
"""
from koscom_scraper_v3 import extract_generic_vin

# Real codes seen in bikes.json (Honda/Suzuki/Kawasaki) + representative
# digit-leading Yamaha codes. Serials are illustrative for the Yamahas (their
# real VINs are what this fix recovers), but the PREFIX shape is authentic.
SHOULD_MATCH = {
    # Honda — letter-leading, all already worked
    "MC22-1050671": "MC22-1050671",
    "MC28-1202345": "MC28-1202345",
    "MD24-1205421": "MD24-1205421",
    "MD32-1000689": "MD32-1000689",
    "NC30-1013851": "NC30-1013851",
    "NC35-1001706": "NC35-1001706",
    "AC10-1033980": "AC10-1033980",
    # Suzuki
    "VJ23A-101119": "VJ23A-101119",
    "VJ22-108842": "VJ22-108842",
    # Kawasaki — 3-digit + trailing letter prefixes
    "MX080B-022368": "MX080B-022368",
    "ZX400L-048080": "ZX400L-048080",
    # Yamaha — digit-leading (the regression this fix targets)
    "3XV-012345": "3XV-012345",
    "4L3-100200": "4L3-100200",
    "2KR-002345": "2KR-002345",
    "1WG-050505": "1WG-050505",
    "4U0-001234": "4U0-001234",
    "3LN-006789": "3LN-006789",
    "3MA-007890": "3MA-007890",
    "3TJ-1000001": "3TJ-1000001",
    "1KT-012121": "1KT-012121",
}

# Non-VIN strings that appear on auction pages and must NOT be matched.
SHOULD_NOT_MATCH = [
    "auction 2026-07-15 lot",     # auctionDate (all-digit prefix)
    "first reg 1989-01-08",       # a date
    "lot 2038850534 BDS",         # lot number (no hyphen)
    "tel 03-1234-5678",           # phone-ish (digit prefix, no letter)
    "2-stroke engine",            # digit prefix, no letter
    "4-stroke",
    "model GSX-R400R here",       # serial begins with a letter
    "grade KR-1S trim",           # serial < 3 digits
    "listed as TZR250-3",         # serial < 3 digits
    "the R-1Z model",             # serial < 3 digits
    "code ABC-1234 x",            # prefix has no digit
    "12,477 km",                  # no hyphen
    "07-15 close",                # serial < 3 digits / digit prefix
]


def test_all_real_codes_match():
    for text, expected in SHOULD_MATCH.items():
        got = extract_generic_vin(text)
        assert got == expected, f"{text!r}: expected {expected!r}, got {got!r}"


def test_real_codes_match_embedded_in_page_text():
    # The scraper searches full page text, not an isolated token.
    text = "Frame No. 3XV-012345 · mileage 12,477 km · grade 4 · lot 2038850534"
    assert extract_generic_vin(text) == "3XV-012345"


def test_non_vin_text_does_not_match():
    for text in SHOULD_NOT_MATCH:
        got = extract_generic_vin(text)
        assert got is None, f"{text!r}: expected no match, got {got!r}"


def test_date_before_vin_is_skipped_vin_still_found():
    # A date precedes the VIN on the page; the date must not be picked.
    text = "auction 2026-07-15 ... Frame 4L3-100200 ..."
    assert extract_generic_vin(text) == "4L3-100200"


def test_missing_returns_none():
    assert extract_generic_vin("no frame number anywhere here") is None
    assert extract_generic_vin("") is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed "
          f"({len(SHOULD_MATCH)} real codes, {len(SHOULD_NOT_MATCH)} negatives)")
