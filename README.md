# Moto2 Imports — Auction Pipeline v3

## What changed vs v2 (the three blockers, solved)

1. **Hotlink blocking (photos 403 in browser).** bdsc.jupiter.ac filters on the
   `Referer` header. Server-side downloads now send `Referer: https://auc.koscom-trade.com/`
   (see `MEDIA_HEADERS` in `koscom_common.py`). Browser-side, the widget routes
   photos through `image-proxy-worker.js` on Cloudflare Workers, which sets the
   same header and edge-caches every image. Free tier is 100k requests/day,
   permanent — no Netlify credit games.
2. **Video SSL failure.** bdsc.jupiter.ac uses legacy TLS renegotiation.
   `LegacySSLAdapter` fixes the handshake — videos now download in Python.
3. **Duplicate listings.** Root cause: each search-result card has multiple
   `<a>` tags to the same bike. v3 dedupes by listing ID across the whole run
   and paginates until a page yields no new IDs.

## Files

| File | Purpose |
|---|---|
| `koscom_common.py` | Session/SSL/Referer + ordered photo/video extraction |
| `koscom_scraper_v3.py` | Daily scraper → `bikes.json` (reads `models.json`) |
| `media_downloader.py` | One-click full package per bike (photos, videos, details.json/txt) |
| `watchlist_outreach.py` | Match bikes ↔ customers, auto-draft outreach emails |
| `models.json` | Your target model list (config, expandable) |
| `watchlist.example.json` | Copy to `watchlist.json` and add real customers |
| `image-proxy-worker.js` | Cloudflare Worker media proxy for the website widget |
| `scraper.yml` | GitHub Actions: daily scrape → outreach drafts → commit |

## Daily flow

```
GitHub Actions (midnight UTC)
  └─ koscom_scraper_v3.py  → bikes.json (committed)
  └─ watchlist_outreach.py → outreach/*.txt drafts + notified.json ledger
Website widget → fetches bikes.json from GitHub raw URL
              → photos via https://YOUR-WORKER.workers.dev/?url=...
You (manual, when needed)
  └─ python3 media_downloader.py <bike-id>      # full 31-photo + video package
  └─ python3 media_downloader.py --validate-only <bike-id>   # HEAD-check URLs
  └─ review outreach drafts → send (or run with --send once trusted)
```

## First-run checklist

1. `pip install requests beautifulsoup4`
2. `python3 koscom_scraper_v3.py --model CBR250RR` — verify against a live
   listing: VIN, mileage, price, grade patterns, and that ~31 photos + 2 videos
   extract. (The grade regexes assume English labels like "Engine Grade";
   adjust in `grade_patterns` if koscom's markup differs.)
3. `python3 media_downloader.py <id-from-bikes.json>` — confirm photos AND
   videos land in `downloads/`.
4. Deploy `image-proxy-worker.js` at dash.cloudflare.com → Workers, then point
   the widget's photo URLs at `https://YOUR-WORKER.workers.dev/?url=` +
   `encodeURIComponent(photoUrl)`.
5. Copy `watchlist.example.json` → `watchlist.json`, add real customers,
   run `python3 watchlist_outreach.py --no-media`.

## Notes

- Nothing is hardcoded: auction house, VIN, mileage, price, grades, dates,
  media URLs are all extracted from the listing page. Missing = 0/"Unknown".
- Outreach is **draft-first**. `--send` requires SMTP env vars
  (`MOTO2_SMTP_HOST/PORT/USER/PASS`, `MOTO2_FROM_EMAIL`). If sending through
  Google Workspace, use an app password and keep volume modest to protect the
  domain reputation you fixed with SPF/DKIM/DMARC.
- BDS Jupiter direct scraping: not recommended. It's a members-only system
  behind your login; automating against it risks your BDS membership, and the
  media it serves is the same CDN koscom exposes publicly. koscom + Referer
  gets you everything with none of the account risk.
