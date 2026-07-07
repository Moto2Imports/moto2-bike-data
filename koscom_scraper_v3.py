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
    extract_photos,
    extract_videos,
    listing_id_from_url,
    make_session,
    slugify,
)

MAX_PAGES_PER_MODEL = 20          # safety cap
REQUEST_DELAY_SECONDS = 1.0       # be polite; this runs once a day
SEARCH_TIMEOUT = 15

# Generic JDM frame-number pattern (e.g. MC22-1053594, 3MA-021xxx, NC30-10x).
# Used only when models.json doesn't supply a vin_prefix.
GENERIC_VIN_RE = re.compile(r"\b([A-Z]{1,4}\d{1,3}[A-Z]?)-(\d{3,8})\b")


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
    def scrape_listing(self, url, make, model, vin_prefix=None, engine_cc=None):
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
            m = GENERIC_VIN_RE.search(page_text)
            if m:
                vin = f"{m.group(1)}-{m.group(2)}"

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

        photos = extract_photos(html)
        videos = extract_videos(html)

        bike = {
            "id": f"{slugify(make)}-{slugify(model)}-{listing_id}",
            "make": make,
            "model": model,
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
              f"{len(photos)} photos / {len(videos)} videos — {auction_house}")
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
            for url in self.search_listing_urls(make, model):
                bike = self.scrape_listing(url, make, model, vin_prefix, engine_cc)
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
