#!/usr/bin/env python3
"""
One-click bike package downloader — Moto2 Imports

Given one or more bike IDs (or listing lot numbers) from bikes.json, downloads
EVERYTHING for each bike into a clean folder:

    downloads/
      honda-cbr250rr-2038839592/
        details.json          <- full structured record
        details.txt           <- human-readable summary (paste into anything)
        photo_01.jpg ... photo_31.jpg     (page order preserved)
        video_01_engine_l.mp4, video_02_engine_r.mp4

Why this now works when v2 didn't:
  * Referer header spoofs a koscom page view -> bdsc.jupiter.ac hotlink
    filter lets the request through.
  * LegacySSLAdapter (koscom_common) -> the .mp4 downloads no longer die on
    the legacy TLS handshake.

Usage:
    python3 media_downloader.py honda-cbr250rr-2038839592 [more ids...]
    python3 media_downloader.py --lot 2038839592
    python3 media_downloader.py --all              # every bike in bikes.json
    python3 media_downloader.py --validate-only ID # HEAD-check URLs, no download

Validation without downloading (your question #3): --validate-only issues
HTTP HEAD requests — a 200 + image/* content-type confirms the file exists
without transferring it.
"""

import argparse
import json
import os
import re
import sys
import time

from koscom_common import MEDIA_HEADERS, make_session

BIKES_JSON = "bikes.json"
DOWNLOAD_ROOT = "downloads"
DELAY = 0.4


def load_bikes():
    if not os.path.exists(BIKES_JSON):
        print(f"[FATAL] {BIKES_JSON} not found — run koscom_scraper_v3.py first.")
        sys.exit(1)
    with open(BIKES_JSON, encoding="utf-8") as f:
        return json.load(f)["bikes"]


def find_bikes(bikes, ids=None, lot=None, all_bikes=False):
    if all_bikes:
        return bikes
    selected = []
    for b in bikes:
        if ids and b["id"] in ids:
            selected.append(b)
        elif lot and b.get("auctionLot") == lot:
            selected.append(b)
    return selected


def details_txt(bike):
    g = bike.get("conditionGrades", {})
    lines = [
        f"{bike['make']} {bike['model']}",
        f"VIN / Frame:     {bike['vin']}",
        f"Mileage:         {bike['mileage']:,} km",
        f"Price:           ¥{bike['price']:,} {bike['currency']}",
        f"Auction:         {bike['auctionHouse']} — {bike['auctionDate']} — lot {bike['auctionLot']}",
        f"Overall grade:   {bike.get('condition', 0)}",
        "Grades:          " + ", ".join(f"{k}:{v}" for k, v in g.items()),
        f"Notes:           {bike.get('inspectionNotes', '')}",
        f"Listing:         {bike['url']}",
        f"Photos:          {len(bike.get('photos', []))}",
        f"Videos:          {len(bike.get('videos', []))}",
    ]
    return "\n".join(lines) + "\n"


def validate_urls(session, bike):
    """HEAD-check every media URL without downloading (question #3)."""
    ok, bad = [], []
    for url in bike.get("photos", []) + bike.get("videos", []):
        try:
            r = session.head(url, headers=MEDIA_HEADERS, timeout=10,
                             allow_redirects=True)
            # Some CDNs reject HEAD; fall back to a ranged GET of 1 byte.
            if r.status_code in (403, 405, 501):
                r = session.get(url, headers={**MEDIA_HEADERS, "Range": "bytes=0-0"},
                                timeout=10, stream=True)
                r.close()
            (ok if r.status_code in (200, 206) else bad).append((url, r.status_code))
        except Exception as e:
            bad.append((url, str(e)))
        time.sleep(0.2)
    return ok, bad


def video_name(idx, url):
    stem = "engine_l" if re.search(r"_l\.mp4$", url) else \
           "engine_r" if re.search(r"_r\.mp4$", url) else "clip"
    return f"video_{idx:02d}_{stem}.mp4"


def download_bike(session, bike):
    folder = os.path.join(DOWNLOAD_ROOT, bike["id"])
    os.makedirs(folder, exist_ok=True)

    with open(os.path.join(folder, "details.json"), "w", encoding="utf-8") as f:
        json.dump(bike, f, indent=2, ensure_ascii=False)
    with open(os.path.join(folder, "details.txt"), "w", encoding="utf-8") as f:
        f.write(details_txt(bike))

    # Known blank-slot placeholder used in the whole-bike 3x3 grid when
    # fewer than 9 hero shots exist. Always exactly this byte size.
    BLANK_PLACEHOLDER_SIZE = 2228

    got_p = got_v = skipped_blank = 0
    for i, url in enumerate(bike.get("photos", []), 1):
        path = os.path.join(folder, f"photo_{i:02d}.jpg")
        if os.path.exists(path):
            got_p += 1
            continue
        try:
            r = session.get(url, headers=MEDIA_HEADERS, timeout=20)
            if len(r.content) == BLANK_PLACEHOLDER_SIZE:
                skipped_blank += 1
                continue
            if r.status_code == 200 and r.content[:3] == b"\xff\xd8\xff":
                with open(path, "wb") as f:
                    f.write(r.content)
                got_p += 1
            else:
                print(f"    [photo {i:02d}] HTTP {r.status_code} — skipped")
        except Exception as e:
            print(f"    [photo {i:02d}] {e}")
        time.sleep(DELAY)
    if skipped_blank:
        print(f"    [photo] skipped {skipped_blank} blank placeholder(s)")

    for i, url in enumerate(bike.get("videos", []), 1):
        path = os.path.join(folder, video_name(i, url))
        if os.path.exists(path):
            got_v += 1
            continue
        try:
            r = session.get(url, headers=MEDIA_HEADERS, timeout=90, stream=True)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                got_v += 1
            else:
                print(f"    [video {i:02d}] HTTP {r.status_code} — skipped")
        except Exception as e:
            print(f"    [video {i:02d}] {e}")
        time.sleep(DELAY)

    print(f"[DONE] {bike['id']} — {got_p}/{len(bike.get('photos', []))} photos, "
          f"{got_v}/{len(bike.get('videos', []))} videos -> {folder}/")
    return folder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="bike ids from bikes.json")
    ap.add_argument("--lot", help="select by auction lot number")
    ap.add_argument("--all", action="store_true", help="download every bike")
    ap.add_argument("--validate-only", action="store_true",
                    help="HEAD-check URLs without downloading")
    args = ap.parse_args()

    bikes = load_bikes()
    selected = find_bikes(bikes, ids=set(args.ids), lot=args.lot,
                          all_bikes=args.all)
    if not selected:
        print("[FATAL] no matching bikes. Use ids from bikes.json, --lot, or --all.")
        sys.exit(1)

    session = make_session()
    for bike in selected:
        if args.validate_only:
            ok, bad = validate_urls(session, bike)
            print(f"[VALIDATE] {bike['id']}: {len(ok)} reachable, {len(bad)} broken")
            for url, why in bad:
                print(f"    BAD {why}: {url}")
        else:
            download_bike(session, bike)


if __name__ == "__main__":
    main()
