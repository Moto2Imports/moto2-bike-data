#!/usr/bin/env python3
"""
Customer Watchlist & Outreach Engine — Moto2 Imports

Replaces the manual loop of: search models -> download media -> write email.

Flow (run after the daily scrape, or chain it in the GitHub Action):
  1. Load bikes.json (fresh scrape) and watchlist.json (your customers).
  2. Match every bike against every customer's criteria
     (make/model, max mileage, max price, min overall grade).
  3. Skip bikes already sent to that customer (notified.json ledger).
  4. Auto-download the full media package for each newly matched bike
     (via media_downloader).
  5. Generate a ready-to-send email draft per customer in outreach/,
     covering ALL their new matches in one email (not one email per bike).
  6. Optionally send via SMTP (--send) using environment variables.

Draft-first by default — you review before anything goes to a customer.

SMTP env vars (only needed with --send):
    MOTO2_SMTP_HOST, MOTO2_SMTP_PORT (587), MOTO2_SMTP_USER,
    MOTO2_SMTP_PASS, MOTO2_FROM_EMAIL

Usage:
    python3 watchlist_outreach.py                # match + drafts + media
    python3 watchlist_outreach.py --no-media     # drafts only, faster
    python3 watchlist_outreach.py --send         # actually email customers
"""

import argparse
import json
import os
import smtplib
import sys
from datetime import date
from email.message import EmailMessage

BIKES_JSON = "bikes.json"
WATCHLIST_JSON = "watchlist.json"
NOTIFIED_JSON = "notified.json"
OUTREACH_DIR = "outreach"


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def bike_matches(bike, want):
    if want.get("make") and bike["make"].lower() != want["make"].lower():
        return False
    if want.get("model") and bike["model"].lower() != want["model"].lower():
        return False
    if want.get("max_mileage") and bike["mileage"] and bike["mileage"] > want["max_mileage"]:
        return False
    if want.get("max_price") and bike["price"] and bike["price"] > want["max_price"]:
        return False
    if want.get("min_grade") and bike.get("condition", 0) < want["min_grade"]:
        return False
    return True


def bike_block(bike):
    grade = bike.get("condition", 0)
    grade_str = f"Grade {grade}" if grade else "Grade pending"
    price_str = f"¥{bike['price']:,}" if bike["price"] else "price TBA"
    return (
        f"• {bike['make']} {bike['model']} — {bike['vin']}\n"
        f"  {bike['mileage']:,} km | {price_str} start | {grade_str}\n"
        f"  Auction: {bike['auctionHouse']}, {bike['auctionDate']} (lot {bike['auctionLot']})\n"
        f"  Listing: {bike['url']}\n"
    )


def build_email(customer, matches):
    first = customer["name"].split()[0]
    models = sorted({f"{b['make']} {b['model']}" for b in matches})
    subject = (f"New at auction: {models[0]}" if len(models) == 1
               else f"New at auction: {len(matches)} bikes matching your watchlist")

    blocks = "\n".join(bike_block(b) for b in matches)
    body = f"""Hi {first},

{'A bike' if len(matches) == 1 else str(len(matches)) + ' bikes'} matching your watchlist just appeared in the upcoming BDS auctions in Japan:

{blocks}
Full photo sets (~31 per bike) and engine-start videos are ready — reply and I'll send them over, or just tell me which one you want me to pursue and I'll handle the bidding.

Heads up: auction dates are firm, so if one of these is the bike, let me know a day or two ahead so I can get a bid in.

Best,
Tim
Moto2 Imports
www.moto2imports.com
"""
    return subject, body


def send_email(to_addr, subject, body):
    host = os.environ.get("MOTO2_SMTP_HOST")
    user = os.environ.get("MOTO2_SMTP_USER")
    pw = os.environ.get("MOTO2_SMTP_PASS")
    from_addr = os.environ.get("MOTO2_FROM_EMAIL", user)
    port = int(os.environ.get("MOTO2_SMTP_PORT", "587"))
    if not all([host, user, pw]):
        print("[FATAL] --send requires MOTO2_SMTP_HOST/USER/PASS env vars")
        sys.exit(1)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = from_addr, to_addr, subject
    msg.set_content(body)
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="send via SMTP instead of drafting only")
    ap.add_argument("--no-media", action="store_true", help="skip media downloads")
    args = ap.parse_args()

    bikes = load_json(BIKES_JSON, {}).get("bikes", [])
    watchlist = load_json(WATCHLIST_JSON, [])
    notified = load_json(NOTIFIED_JSON, {})   # {customer_email: [bike ids]}

    if not bikes:
        print("[FATAL] bikes.json empty/missing — run the scraper first.")
        sys.exit(1)
    if not watchlist:
        print("[FATAL] watchlist.json missing — copy watchlist.example.json and edit.")
        sys.exit(1)

    os.makedirs(OUTREACH_DIR, exist_ok=True)
    total_matches = 0

    for customer in watchlist:
        email = customer["email"]
        already = set(notified.get(email, []))
        matches = [
            b for b in bikes
            if b["id"] not in already
            and any(bike_matches(b, w) for w in customer["watching"])
        ]
        if not matches:
            continue
        total_matches += len(matches)

        # Media packages for attachments / your records
        if not args.no_media:
            from media_downloader import download_bike
            from koscom_common import make_session
            session = make_session()
            for b in matches:
                download_bike(session, b)

        subject, body = build_email(customer, matches)
        draft_path = os.path.join(
            OUTREACH_DIR,
            f"{date.today().isoformat()}_{email.replace('@', '_at_')}.txt",
        )
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(f"TO: {email}\nSUBJECT: {subject}\n\n{body}")
        print(f"[DRAFT] {customer['name']} — {len(matches)} bike(s) -> {draft_path}")

        if args.send:
            send_email(email, subject, body)
            print(f"[SENT] -> {email}")

        notified.setdefault(email, []).extend(b["id"] for b in matches)

    with open(NOTIFIED_JSON, "w", encoding="utf-8") as f:
        json.dump(notified, f, indent=2)

    print(f"\n[SUMMARY] {total_matches} new customer matches. "
          f"Drafts in {OUTREACH_DIR}/, ledger updated in {NOTIFIED_JSON}.")


if __name__ == "__main__":
    main()
