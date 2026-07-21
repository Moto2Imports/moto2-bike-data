#!/usr/bin/env python3
"""
Koscom Trade Auction Scraper v3 — Moto2 Imports

Fixes vs v2:
  * DEDUPLICATION: each listing ID is scraped exactly once, even when the
    search results page contains 3-4 anchor tags pointing at the same bike
    (thumbnail link, title link, price link...). This was the root cause of
    duplicate entries.
  * PAGINATION: walks page=1,2,3... until a page yields no new listings.
  * NO HARDCODED VALUES: make/model come from models.json config; auction
    house, date, VIN, mileage, price, grades, photos, videos are all
    extracted from the listing page. Fields that can't be found are set to
    explicit "Unknown"/0 sentinels — never invented.
  * VIN: uses per-model vin_prefix from models.json when provided; otherwise
    a generic JDM frame-number pattern. Verify the generic pattern against a
    couple of live listings for each new model you enable.
  * BDS-ONLY filter retained.
  * Photos/videos extracted ordered + deduped via koscom_common.

Usage:
    python3 koscom_scraper_v3.py                 # scrape all models.json entries
    python3 koscom_scraper_v3.py --model CBR250RR  # single model (testing)

Output: bikes.json (same schema your widget already consumes).
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from koscom_common import (
    KOSCOM_BASE_URL,
    MEDIA_HEADERS,
    extract_photos,
    extract_videos,
    has_no_title_marker,
    listing_id_from_url,
    make_session,
    slugify,
)

MAX_PAGES_PER_MODEL = 20          # safety cap
REQUEST_DELAY_SECONDS = 1.0       # be polite; this runs once a day
SEARCH_TIMEOUT = 15

# Generic JDM frame-number pattern (PREFIX-serial), used when models.json has
# no vin_prefix. The prefix is 2-6 alphanumerics that must contain BOTH a letter
# and a digit, so it matches:
#   - letter-leading codes: MC22-, MD24-, NC30-, VJ23A-, MX080B-, ZX400L- …
#   - digit-leading Yamaha codes: 3XV-, 4L3-, 2KR-, 1WG-, 3MA- …  (the old
#     `[A-Z]{1,4}\d…` form required a leading letter and silently missed ALL
#     Yamahas, whose model codes start with a digit)
# The letter+digit requirement excludes non-VIN text: all-digit dates/lot
# numbers ("2026-07-15", "07-15"), pure-letter tokens ("ABC-1234"), and the
# serial's `\d{3,8}` rejects model strings like "TZR250-3" / "KR-1S".
GENERIC_VIN_RE = re.compile(
    r"\b(?=[A-Z0-9]{2,6}-\d)"      # shape gate: 2-6 alnum, hyphen, digit
    r"(?=[A-Z0-9]*[A-Z])"          # prefix contains >=1 letter
    r"(?=[A-Z0-9]*\d)"             # prefix contains >=1 digit
    r"([A-Z0-9]{2,6})-(\d{3,8})\b"
)


def extract_generic_vin(page_text):
    """First PREFIX-serial frame number in the page, or None. Used when the
    model has no configured vin_prefix (see GENERIC_VIN_RE)."""
    m = GENERIC_VIN_RE.search(page_text)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def evaluate_vin_serial_bound(page_text, prefix, max_serial):
    """Soft VIN serial UPPER-BOUND check, for targets that share a VIN prefix
    across sub-generations where only the earlier serials qualify (e.g. Honda
    HORNET250 is all MC31, but only units below MC31-1250000 clear the 25-year
    import line). This is NOT a prefix filter — the search string already
    disambiguates the model; this only bounds the serial.

    Returns (verdict, vin):
        ("keep", "MC31-1249999")  a matching-prefix serial was found, below max
        ("skip", None)            a matching-prefix serial was found, at/above
                                  max — drop as too new
        ("flag", None)            no matching-prefix serial found — the caller
                                  KEEPS the listing but flags it for manual
                                  review (never silently drop a real listing).
    """
    m = re.search(rf"\b{re.escape(prefix)}-(\d{{3,8}})\b", page_text)
    if not m:
        return ("flag", None)
    serial = int(m.group(1))
    if serial >= max_serial:
        return ("skip", None)
    return ("keep", f"{prefix}-{m.group(1)}")


# ---------------------------------------------------------- year extraction --
# BDS/koscom inspection sheets record the model year as a Japanese imperial-era
# date (e.g. 平成7年 / "H7", 昭和63年 / "S63", 令和2年 / "R2"), not a Gregorian
# year. The site's `year` field wants a plain Gregorian integer, so convert:
#
#   Showa  (昭和 / S) = 1925 + n   (S64 = 1989)
#   Heisei (平成 / H) = 1988 + n   (H1  = 1989, H31 = 2019)
#   Reiwa  (令和 / R) = 2018 + n   (R1  = 2019)  — rare given the 25y cutoff
#
# Showa-64 / Heisei-1 boundary (Jan 1-7 vs Jan 8+, 1989): it does NOT affect the
# Gregorian YEAR — S64 and H1 are both 1989 (as are H31 and R1, both 2019) — so
# no month is needed to compute `year`. A month would only pin the era *label*,
# which is not emitted. Missing/unparseable → None (JSON null → site "N/A"),
# consistent with the "never invent a value" rule.
#
# NOTE: the exact sheet label/markup for the year row could not be confirmed
# against a live listing (the auction site is unreachable from this env and the
# repo ships no HTML fixture). Extraction is written against the known sheet
# vocabulary + the existing td.bkth spec-table structure and MUST be verified on
# a live run: `python3 koscom_scraper_v3.py --model CBR250RR`.
ERA_BASE = {"S": 1925, "H": 1988, "R": 2018,
            "昭和": 1925, "平成": 1988, "令和": 2018}

# Highest valid year-number per era (Showa ended year 64, Heisei year 31, Reiwa
# is ongoing). Bounds the era number so a stray "S99" can't parse to a year.
ERA_MAX = {"S": 64, "H": 31, "昭和": 64, "平成": 31}

ERA_KANJI_RE = re.compile(r"(昭和|平成|令和)\s*(元|\d{1,2})\s*年?")
ERA_ABBR_RE = re.compile(r"\b([SHR])\.?\s*(\d{1,2})\b")
GREGORIAN_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")

# Spec-table labels that carry the model year / first-registration date. Covers
# koscom's English-ish labels (the grade labels render as "engine", "frame"...)
# and the Japanese sheet vocabulary. Grade-row labels never contain these, so
# there is no collision with the condition-grade table.
YEAR_LABELS = ("year", "model year", "registration", "first registration",
               "年式", "初度登録", "初度", "登録年月", "登録")


def _era_number(token):
    """'元' (first year) -> 1; otherwise the integer value."""
    return 1 if token == "元" else int(token)


def era_to_gregorian(era, n):
    """Convert an era code + number to a Gregorian year, or None if out of range.
    Rejects nonsense era numbers (e.g. 'S99') via each era's real span and a
    final clamp at the current year (Reiwa is open-ended)."""
    base = ERA_BASE.get(era)
    if base is None or n < 1:
        return None
    # Reiwa has no ERA_MAX entry (ongoing) — the year clamp below bounds it.
    if era in ERA_MAX and n > ERA_MAX[era]:
        return None
    year = base + n
    return year if 1955 <= year <= datetime.now().year else None


def parse_year_from_value(text):
    """Parse a spec-cell value holding a model year: kanji era (平成7年),
    abbreviated era (H7 / S63 / R2), or a bare Gregorian year (1995)."""
    if not text:
        return None
    for regex in (ERA_KANJI_RE, ERA_ABBR_RE):
        m = regex.search(text)
        if m:
            year = era_to_gregorian(m.group(1), _era_number(m.group(2)))
            if year:
                return year
    m = GREGORIAN_YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def parse_year_from_text(text):
    """Whole-page fallback. Only the unambiguous kanji-era form is trusted here;
    a bare 'H7'/'S63' or 4-digit number would collide with VINs, grades, the
    auction date, etc. elsewhere on the page."""
    m = ERA_KANJI_RE.search(text or "")
    if m:
        return era_to_gregorian(m.group(1), _era_number(m.group(2)))
    return None


def extract_year(soup, page_text):
    """Model year as a Gregorian int, or None. Prefers the spec-table year row
    (same td.bkth structure the grades use); falls back to a kanji-era scan."""
    for label_td in soup.find_all("td", class_="bkth"):
        label = label_td.get_text(" ", strip=True).lower()
        if any(k in label for k in YEAR_LABELS):
            value_td = label_td.find_next_sibling("td")
            if value_td:
                year = parse_year_from_value(value_td.get_text(" ", strip=True))
                if year:
                    return year
    return parse_year_from_text(page_text)


# --------------------------------------------------------- photo filtering --
# Auction photos arrive as a fixed 33-slot set: a 9-slot hero grid on the
# tru.ru / ajes CDNs (whole-bike shots ~1-6, accessory/extra ~7-9) followed by
# 24 bdsc inspection slots (~10-33). Unused slots hold placeholders, confirmed
# by fingerprint across many live listings:
#   * hero blank  — HTTP 200, exactly 2228 bytes (sha256 7bd8e2ebc926…)
#   * inspection  — HTTP 404 (bdsc "not yet inspected"), before the sheet lands
# Placeholders are detected by fetched CONTENT, not slot position (a real photo
# can land in an unexpected slot). Survivors are ordered whole-bike →
# inspection → any real accessory shots last.
HERO_BLANK_SIZE = 2228
HERO_BLANK_SHA_PREFIX = "7bd8e2ebc926"
_HERO_HOST_RE = re.compile(r"^\d+\.(?:tru\.ru|ajes\.com)$")


def _photo_host(url):
    parts = url.split("/")
    return parts[2] if len(parts) > 2 else ""


def is_hero_photo(url):
    """True for whole-bike / accessory hero-grid shots (tru.ru / ajes CDNs);
    False for bdsc inspection shots."""
    return bool(_HERO_HOST_RE.match(_photo_host(url)))


def is_placeholder_photo(session, url):
    """Fetch-check a photo URL: True when it's an unpopulated placeholder — a
    bdsc 404 inspection slot, or the 2228-byte hero blank. HEAD first; only GET
    when a blank is suspected or HEAD is unsupported. Network errors → False
    (keep it — a transient blip must never drop a real image)."""
    try:
        h = session.head(url, headers=MEDIA_HEADERS, timeout=12, allow_redirects=True)
        if h.status_code == 404:
            return True
        if h.status_code not in (403, 405, 501):  # HEAD is supported
            clen = h.headers.get("Content-Length")
            if clen is not None and int(clen) != HERO_BLANK_SIZE:
                return False  # real: not a 404, and not the tiny hero blank
        # HEAD unsupported / no Content-Length / size == blank → confirm via GET
        g = session.get(url, headers=MEDIA_HEADERS, timeout=15)
        if g.status_code == 404:
            return True
        body = g.content
        return (len(body) == HERO_BLANK_SIZE
                and hashlib.sha256(body).hexdigest().startswith(HERO_BLANK_SHA_PREFIX))
    except Exception:
        return False


def filter_and_order_photos(photos, is_placeholder):
    """Drop placeholders and order survivors: whole-bike hero shots (first 6
    hero) → inspection shots (bdsc) → any remaining hero/accessory shots last.
    `is_placeholder(url) -> bool` is injected so the ordering is unit-testable.
    Ordering keys on host, not slot index, so it survives odd photo counts."""
    kept = [u for u in photos if not is_placeholder(u)]
    hero = [u for u in kept if is_hero_photo(u)]
    inspection = [u for u in kept if not is_hero_photo(u)]
    whole_bike, accessory = hero[:6], hero[6:]
    return whole_bike + inspection + accessory


def load_models(path="models.json"):
    if not os.path.exists(path):
        print(f"[FATAL] {path} not found. Create it with your 56-model list.")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["models"] if isinstance(data, dict) else data


class KoscomScraperV3:
    def __init__(self):
        self.session = make_session()
        self.bikes = []
        self.seen_listing_ids = set()   # <-- dedup across the entire run

    # ------------------------------------------------------------------ search
    def search_listing_urls(self, make, model):
        """Walk paginated search results; return unique listing URLs."""
        urls = []
        for page in range(1, MAX_PAGES_PER_MODEL + 1):
            params = {"manuf": make, "model": model, "page": page}
            url = f"{KOSCOM_BASE_URL}/bike?{urlencode(params)}"
            try:
                resp = self.session.get(url, timeout=SEARCH_TIMEOUT)
                resp.encoding = "utf-8"
            except Exception as e:
                print(f"[ERROR] search page {page} failed: {e}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            page_ids = set()
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/bike-" in href:
                    lid = listing_id_from_url(href)
                    if lid not in self.seen_listing_ids and lid not in page_ids:
                        page_ids.add(lid)
                        full = href if href.startswith("http") else KOSCOM_BASE_URL + href
                        urls.append(full)

            if not page_ids:            # no NEW listings -> done paginating
                break
            self.seen_listing_ids.update(page_ids)
            time.sleep(REQUEST_DELAY_SECONDS)

        print(f"[SEARCH] {make} {model}: {len(urls)} unique listings")
        return urls

    # ----------------------------------------------------------------- scrape
    def scrape_listing(self, url, make, model, vin_prefix=None, engine_cc=None,
                       vin_serial_max=None):
        # `model` is BOTH the koscom search string AND the raw feed name written
        # to bikes.json — the site (moto2-site) keys model-lookup.json on this
        # exact string to attach the canonical model + trim + chassis for
        # display, so it must stay the raw search string, never a display label.
        try:
            resp = self.session.get(url, timeout=SEARCH_TIMEOUT)
            resp.encoding = "utf-8"
        except Exception as e:
            print(f"[ERROR] {url}: {e}")
            return None

        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        listing_id = listing_id_from_url(url)

        # ---- BDS-only filter, and EXTRACT the house (never hardcode it)
        house_match = re.search(r"\bBDS[\s\u00a0]+[A-Za-z\u3040-\u30ff\u4e00-\u9fff]+", page_text)
        if not house_match:
            if "BDS" not in page_text:
                print(f"[SKIP] {listing_id}: not a BDS auction")
                return None
            auction_house = "BDS"
        else:
            auction_house = re.sub(r"\s+", " ", house_match.group(0)).strip()

        # ---- Title / registration certificate present? koscom marks bikes sold
        # WITHOUT their certificate as "SHO LOUIS NOT EQUIPPED" (in the model
        # title). Scanned over the whole page and surfaced as a boolean.
        has_title = not has_no_title_marker(page_text)

        # ---- VIN
        vin = "Unknown"
        if vin_prefix:
            m = re.search(rf"\b{re.escape(vin_prefix)}-(\d{{3,8}})\b", page_text)
            if m:
                vin = f"{vin_prefix}-{m.group(1)}"
            else:
                print(f"[SKIP] {listing_id}: no VIN matching prefix {vin_prefix}")
                return None
        else:
            vin = extract_generic_vin(page_text) or "Unknown"

        # ---- VIN serial UPPER BOUND (soft include, not a prefix filter).
        # Some targets share a VIN prefix across sub-generations where only the
        # earlier (25-year-eligible) serials qualify — e.g. Honda HORNET250 is
        # all MC31, but only units below MC31-1250000 clear the import line.
        # `vin_serial_max = {"prefix": "MC31", "max": 1250000}` keeps a listing
        # ONLY when a matching-prefix frame number is found AND its serial is
        # below the bound. A serial at/above the bound is dropped as too new.
        # A listing with NO extractable matching-prefix serial is KEPT and
        # flagged (never silently drop a real listing — same rule as the photo
        # filter and the missing-data N/A fallback); a human reviews the WARN.
        if vin_serial_max:
            prefix = vin_serial_max["prefix"]
            max_serial = vin_serial_max["max"]
            verdict, bound_vin = evaluate_vin_serial_bound(page_text, prefix, max_serial)
            if verdict == "skip":
                print(f"[SKIP] {listing_id}: {model} "
                      f"{prefix}-serial >= bound {prefix}-{max_serial} (too new)")
                return None
            elif verdict == "keep":
                vin = bound_vin          # pin VIN to the bound-checked frame no.
            else:                        # "flag" — keep, but surface for review
                print(f"[WARN] {listing_id}: {model} no {prefix} serial "
                      f"found — cannot verify < {prefix}-{max_serial}, "
                      f"keeping for manual review")

        # ---- Mileage: take the LARGEST km figure on the page. Detail pages
        # sometimes show partial/odometer-note numbers; the true total is the
        # max. (This also survives the old '35,874 -> 874' truncation class
        # of bug because commas are captured.)
        mileage = 0
        km_values = [
            int(v.replace(",", ""))
            for v in re.findall(r"(\d{1,3}(?:,\d{3})*|\d+)\s*km\b", page_text, re.IGNORECASE)
        ]
        if km_values:
            mileage = max(km_values)

        # ---- Price
        price = 0
        pm = re.search(r"([\d,]{3,12})\s*(?:JPY|yen|\u5186)", page_text, re.IGNORECASE)
        if pm:
            try:
                price = int(pm.group(1).replace(",", ""))
            except ValueError:
                pass

        # ---- Auction date
        auction_date = "Unknown"
        dm = re.search(r"\b(\d{4}[-/]\d{2}[-/]\d{2})\b", page_text)
        if dm:
            auction_date = dm.group(1).replace("/", "-")

        # ---- Model year (Japanese era → Gregorian int; None if not found)
        year = extract_year(soup, page_text)

        # ---- Condition grades (extracted; 0 = not found on page)
        condition_grades = {k: 0 for k in
                            ("general", "frame", "engine", "electro",
                             "exterior", "front", "rear")}
        grade_labels = {"general", "frame", "engine", "electro",
                        "exterior", "front", "rear"}
        for label_td in soup.find_all("td", class_="bkth"):
            label = label_td.get_text(strip=True).lower()
            if label not in grade_labels:
                continue
            value_td = label_td.find_next_sibling("td")
            if not value_td:
                continue
            m = re.match(r"(\d(?:\.\d)?)", value_td.get_text(strip=True))
            if m:
                val = m.group(1)
                condition_grades[label] = float(val) if "." in val else int(val)

        # ---- Inspection notes: per-category breakdown (Engine, Electro, etc.)
        # Each grade category has its own <div class=score_title> (name + score)
        # followed by a <div class=score_notes> listing component condition notes.
        note_sections = []
        for title_div in soup.find_all("div", class_="score_title"):
            category = title_div.get_text(" ", strip=True)
            category = re.sub(r"\s*\d+\s*$", "", category).strip()  # drop trailing score digit
            notes_div = title_div.find_next_sibling("div", class_="score_notes")
            if not notes_div:
                continue
            items = []
            for b in notes_div.find_all("b"):
                label = b.get_text(strip=True)
                # text after the <b> up to the next <br> or <b>
                tail = b.next_sibling
                detail = ""
                while tail and getattr(tail, "name", None) != "br" and getattr(tail, "name", None) != "b":
                    detail += str(tail) if isinstance(tail, str) else tail.get_text()
                    tail = tail.next_sibling
                detail = re.sub(r"\s+", " ", detail).strip(" -")
                if label:
                    items.append(f"{label}: {detail}" if detail else label)
            if items:
                note_sections.append(f"{category} — " + "; ".join(items))
        inspection_notes = " | ".join(note_sections)

        photos = filter_and_order_photos(
            extract_photos(html),
            lambda u: is_placeholder_photo(self.session, u),
        )
        videos = extract_videos(html)

        bike = {
            "id": f"{slugify(make)}-{slugify(model)}-{listing_id}",
            "make": make,
            "model": model,
            "year": year,
            "hasTitle": has_title,
            "vin": vin,
            "mileage": mileage,
            "price": price,
            "currency": "JPY",
            "condition": condition_grades["general"],
            "auctionDate": auction_date,
            "auctionHouse": auction_house,
            "auctionLot": listing_id,
            "engine": engine_cc or 0,
            "status": "available",
            "photos": photos,
            "videos": videos,
            "conditionGrades": condition_grades,
            "inspectionNotes": inspection_notes or "Check listing for detailed inspection notes",
            "url": url,
        }
        print(f"[OK] {make} {model} {vin} — {mileage:,}km — ¥{price:,} — "
              f"{len(photos)} photos / {len(videos)} videos — {auction_house}"
              f"{'' if has_title else ' — [NO TITLE]'}")
        return bike

    # -------------------------------------------------------------------- run
    def run(self, only_model=None):
        models = load_models()
        if only_model:
            models = [m for m in models
                      if m["model"].lower() == only_model.lower()]
            if not models:
                print(f"[FATAL] model '{only_model}' not in models.json")
                sys.exit(1)

        print(f"[START] {datetime.now().isoformat()} — {len(models)} model(s)")
        for spec in models:
            make, model = spec["make"], spec["model"]
            vin_prefix = spec.get("vin_prefix")
            engine_cc = spec.get("engine_cc")
            vin_serial_max = spec.get("vin_serial_max")
            for url in self.search_listing_urls(make, model):
                bike = self.scrape_listing(url, make, model, vin_prefix, engine_cc,
                                           vin_serial_max=vin_serial_max)
                if bike:
                    self.bikes.append(bike)
                time.sleep(REQUEST_DELAY_SECONDS)

        out = {
            "lastUpdated": datetime.now().isoformat(),
            "totalBikes": len(self.bikes),
            "bikes": self.bikes,
        }
        with open("bikes.json", "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"\n[SAVED] bikes.json — {len(self.bikes)} bikes "
              f"({len(self.seen_listing_ids)} listings inspected, duplicates removed)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="scrape a single model for testing")
    args = ap.parse_args()
    KoscomScraperV3().run(only_model=args.model)
