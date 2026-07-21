#!/usr/bin/env python3
"""
Shared helpers for the Moto2 Imports auction pipeline.

Key fixes vs v2:
  1. LegacySSLAdapter  -> solves the bdsc.jupiter.ac SSL handshake failure
                          (legacy TLS renegotiation), enabling video downloads.
  2. Referer header    -> solves the 403/hotlink blocking on bdsc.jupiter.ac
                          media when fetched server-side.
  3. Ordered photo extraction from window.photoswipe_items with regex fallback,
     de-duplicated while PRESERVING page order (photo_01 stays photo_01).

Nothing here hardcodes bike data. All values are extracted from source pages.
"""

import json
import re
import ssl

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

KOSCOM_BASE_URL = "https://auc.koscom-trade.com"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# bdsc.jupiter.ac serves media to pages hosted on koscom-trade.com, so a
# koscom Referer is what its hotlink filter expects.
MEDIA_HEADERS = {
    **BROWSER_HEADERS,
    "Referer": KOSCOM_BASE_URL + "/",
}

# ---- "No title / registration certificate not included" marker --------------
# koscom tags such listings with the phrase "SHO LOUIS NOT EQUIPPED", which
# appears as part of the displayed model name/title (e.g. "HONDA / CRF250L SHO
# LOUIS NOT EQUIPPED"). It means the bike ships WITHOUT its title/registration
# certificate — material for a buyer — so we surface it as a boolean flag and
# strip the phrase out of the displayed model name.
#
# Detection runs over the whole page text (location-agnostic); confirmed on a
# live listing to sit in the title, so a whole-page scan reliably catches it.
# Case-insensitive, whitespace-tolerant. Add sibling wordings here as they
# surface in real data.
NO_TITLE_PATTERNS = (
    r"SHO\s+LOUIS\s+NOT\s+EQUIPPED",
)
NO_TITLE_RE = re.compile("|".join(NO_TITLE_PATTERNS), re.IGNORECASE)


def has_no_title_marker(text):
    """True when `text` contains a no-title marker (SHO LOUIS NOT EQUIPPED / a
    listed variant). Safe on None/empty."""
    return bool(text) and NO_TITLE_RE.search(text) is not None


def strip_no_title_marker(text):
    """Return (clean_text, had_marker): remove the no-title marker and tidy any
    orphaned separators/whitespace, so the phrase never rides along in the
    displayed model name (the boolean flag carries the fact instead)."""
    if not text:
        return text, False
    if NO_TITLE_RE.search(text) is None:
        return text, False
    cleaned = NO_TITLE_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—/·,")
    return cleaned, True


class LegacySSLAdapter(HTTPAdapter):
    """Requests adapter that tolerates legacy TLS servers (bdsc.jupiter.ac).

    Enables OP_LEGACY_SERVER_CONNECT (unsafe legacy renegotiation) and lowers
    OpenSSL security level so old cipher suites/key sizes are accepted.
    """

    def _ctx(self):
        ctx = create_urllib3_context()
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        try:
            ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        except ssl.SSLError:
            pass
        # Old CDN certs frequently fail modern verification; media integrity
        # risk here is acceptable (public auction photos), so relax it.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._ctx()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._ctx()
        return super().proxy_manager_for(*args, **kwargs)


def make_session() -> requests.Session:
    """Session with browser headers + legacy-SSL support for bdsc.jupiter.ac."""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    s.mount("https://bdsc.jupiter.ac", LegacySSLAdapter())
    # requests re-imposes its own cert verification on top of any custom
    # ssl_context unless the session itself opts out. bdsc.jupiter.ac's
    # legacy/broken cert chain needs this; public auction media only.
    s.verify = False
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return s


def ordered_dedupe(items):
    """Remove duplicates while preserving first-seen order."""
    return list(dict.fromkeys(items))


def extract_photos(html: str):
    """Return photo URLs in page order (hero shots first, then detail shots).

    Strategy:
      1. Parse window.photoswipe_items (authoritative, ordered) if present.
         Handles both strict JSON and common JS-literal quirks
         (single quotes, trailing commas).
      2. Regex sweep of ajes.com + bdsc.jupiter.ac URLs, in document order,
         appended for anything photoswipe missed.
    """
    photos = []

    m = re.search(r"window\.photoswipe_items\s*=\s*(\[.*?\])\s*;", html, re.DOTALL)
    if m:
        raw = m.group(1)
        items = None
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            # JS-literal cleanup: 'src' -> "src", trailing commas removed
            cleaned = re.sub(r",\s*([\]}])", r"\1", raw.replace("'", '"'))
            try:
                items = json.loads(cleaned)
            except json.JSONDecodeError:
                items = None
        if items:
            for item in items:
                if isinstance(item, dict):
                    src = item.get("src") or item.get("msrc")
                    if src:
                        photos.append(src)
                elif isinstance(item, str):
                    photos.append(item)
        else:
            # Last-ditch: pull src values straight out of the JS blob, in order
            photos.extend(re.findall(r"""["']?src["']?\s*:\s*["']([^"']+)["']""", raw))

    # Regex sweep in document order (finditer preserves position order).
    # Two photo shapes on this site: detail/close-up shots (bdsc.jupiter.ac
    # or N.ajes.com, end in .jpg/.jpeg/.png) and whole-bike hero shots
    # (N.ajes.com or N.tru.ru, under /imgs/, opaque hash with NO file
    # extension, often suffixed &w=NNN).
    url_pattern = re.compile(
        r"""https?://\d+\.tru\.ru/imgs/[^\s"'<>\\]+&w=\d+"""
        r"""|https?://(?:\d+\.ajes\.com|bdsc\.jupiter\.ac)[^\s"'<>\\]+\.(?:jpg|jpeg|png)""",
        re.IGNORECASE,
    )
    raw_matches = [m.group(0) for m in url_pattern.finditer(html)]
    # Strip the &w=NNN resize parameter from tru.ru whole-bike shots to get
    # the full-resolution original instead of the downsized preview.
    photos.extend(re.sub(r"&w=\d+$", "", u) for u in raw_matches)

    return ordered_dedupe(photos)


def extract_videos(html: str):
    """Return video URLs in page order (engine left/right runs)."""
    pattern = re.compile(r"""https?://[^\s"'<>\\]+\.mp4""", re.IGNORECASE)
    return ordered_dedupe(m.group(0) for m in pattern.finditer(html))


def listing_id_from_url(url: str) -> str:
    """'https://auc.koscom-trade.com/bike-2038839592.htm' -> '2038839592'."""
    m = re.search(r"/bike-([A-Za-z0-9]+)\.htm", url)
    return m.group(1) if m else url.rstrip("/").split("/")[-1]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
