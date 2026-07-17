#!/usr/bin/env python3
"""
Diagnostic: why does browse-by-make (manuf only, no model) return 0 listings
while per-model search works? RUN THIS IN AN ENVIRONMENT WITH KOSCOM ACCESS.

It fetches the known-good per-model URL and several candidate browse URLs side
by side, reporting for each: HTTP status, any redirect (final URL), body size,
total <a> anchors, /bike- listing anchors, and distinct listing IDs. Whichever
candidate returns non-zero /bike- anchors is the correct browse query.

For the current (broken) browse URL it also prints the page <title> and a text
snippet, which usually reveals whether koscom served a search form / landing
page / error instead of results.

    python3 probe_koscom_browse.py            # defaults to Honda
    python3 probe_koscom_browse.py Kawasaki
"""
import sys
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from koscom_common import KOSCOM_BASE_URL, listing_id_from_url, make_session

MAKE = sys.argv[1] if len(sys.argv) > 1 else "Honda"

# label -> query params (page added where relevant). First is the known-good
# per-model query as a control; the rest are browse hypotheses.
CANDIDATES = [
    ("per-model CONTROL (known good)", {"manuf": MAKE, "model": "CBR250RR", "page": 1}),
    ("browse: model OMITTED (current code)", {"manuf": MAKE, "page": 1}),
    ("browse: model EMPTY string", {"manuf": MAKE, "model": "", "page": 1}),
    ("browse: model empty, NO page", {"manuf": MAKE, "model": ""}),
    ("browse: manuf only, NO page", {"manuf": MAKE}),
]


def summarize(html):
    soup = BeautifulSoup(html, "html.parser")
    a_all = soup.find_all("a", href=True)
    bike_hrefs = [a["href"] for a in a_all if "/bike-" in a["href"]]
    ids = {listing_id_from_url(h) for h in bike_hrefs}
    title = (soup.title.get_text(strip=True) if soup.title else "")
    return len(a_all), len(bike_hrefs), len(ids), title, soup


def main():
    session = make_session()
    print(f"=== koscom browse probe for manuf={MAKE!r} ===\n")
    for label, params in CANDIDATES:
        url = f"{KOSCOM_BASE_URL}/bike?{urlencode(params)}"
        try:
            r = session.get(url, timeout=25)
            r.encoding = "utf-8"
            total, bike, ids, title, soup = summarize(r.text)
            redir = f"\n  REDIRECTED -> {r.url}" if r.url != url else ""
            print(f"[{label}]\n  {url}{redir}\n  HTTP {r.status_code} | "
                  f"len {len(r.text)} | total<a> {total} | /bike- {bike} | "
                  f"distinct-ids {ids} | title={title!r}")
            # For the current broken browse query, show what page we actually got.
            if "OMITTED" in label and bike == 0:
                text = soup.get_text(" ", strip=True)
                print(f"  SNIPPET: {text[:300]!r}")
            print()
        except Exception as e:
            print(f"[{label}]\n  {url}\n  ERROR {type(e).__name__}: {e}\n")

    print("Read-off: the candidate with non-zero '/bike-' is the correct browse\n"
          "query. Also please paste the URL your BROWSER shows when you pick\n"
          f"'{MAKE}' with no model in koscom's own search UI — that's definitive.")


if __name__ == "__main__":
    main()
