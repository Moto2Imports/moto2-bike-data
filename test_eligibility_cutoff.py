#!/usr/bin/env python3
"""
Tests for the unified eligibility cutoff + browse year gate.

The cutoff is the ONE source of truth for "is this bike old enough": a dynamic
`current_year - ELIGIBILITY_WINDOW_YEARS` (26), recomputed each run. The browse
year gate keeps a listing ONLY when its year is present and at/below the cutoff.

    python3 test_eligibility_cutoff.py
"""
from koscom_scraper_v3 import (
    ELIGIBILITY_WINDOW_YEARS,
    eligibility_cutoff_year,
    passes_year_gate,
)


def test_window_is_26():
    assert ELIGIBILITY_WINDOW_YEARS == 26


def test_cutoff_is_dynamic_current_minus_window():
    assert eligibility_cutoff_year(2026) == 2000
    assert eligibility_cutoff_year(2027) == 2001   # rolls forward automatically
    assert eligibility_cutoff_year(2030) == 2004


def test_cutoff_defaults_to_now(monkeypatch=None):
    # Without an explicit year it uses datetime.now().year; just assert it's an
    # int 26 below some plausible current year (no hardcoded expectation).
    import datetime as _dt
    assert eligibility_cutoff_year() == _dt.datetime.now().year - 26


def test_gate_missing_year_is_rejected():
    # Browse cannot vouch for an unknown-year bike -> reject (per-model covers it)
    assert passes_year_gate(None, 2000) is False


def test_gate_at_cutoff_is_eligible():
    assert passes_year_gate(2000, 2000) is True     # inclusive: == cutoff kept


def test_gate_below_cutoff_is_eligible():
    assert passes_year_gate(1988, 2000) is True


def test_gate_above_cutoff_is_rejected():
    assert passes_year_gate(2001, 2000) is False
    assert passes_year_gate(2015, 2000) is False


def test_gate_tracks_a_moving_cutoff():
    # Same 2001 bike: ineligible at 2026's cutoff, eligible once cutoff hits 2001.
    assert passes_year_gate(2001, eligibility_cutoff_year(2026)) is False
    assert passes_year_gate(2001, eligibility_cutoff_year(2027)) is True


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed")
