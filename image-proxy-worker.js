/**
 * Moto2 Imports — media proxy (Cloudflare Worker)
 *
 * Solves the browser-side hotlink problem: bdsc.jupiter.ac rejects image
 * requests whose Referer isn't a koscom page. This Worker fetches the media
 * server-side with the right Referer and streams it to your widget with
 * open CORS + 24h edge caching.
 *
 * On top of the proxy it now does two things to the PHOTOS (videos and any
 * non-image response stream through untouched):
 *
 *   1. Enhancement (every photo) — a mild, fixed brightness/contrast/
 *      saturation/sharpen pass so the auction studio shots read cleaner on the
 *      dark site. Knobs live in ENHANCE below.
 *
 *   2. Auto-centering (whole-bike shots only) — when the request carries
 *      `?center=1`, a cheap color heuristic (centering.mjs, NOT an AI model)
 *      finds the bike against the plain studio background and recenters the
 *      frame on it. The site sets `center=1` only on the leading whole-bike
 *      hero shots (see moto2-site adapter `isWholeBikeShot`); inspection/
 *      accessory shots are never flagged, and the heuristic itself no-ops on
 *      anything with a busy/non-plain background (misfire safety valve).
 *
 * Image work uses Photon (Rust→WASM). It needs Workers Paid for CPU headroom;
 * every transform is wrapped so that ANY failure (decode error, oversized
 * image, WASM issue) falls back to streaming the original bytes — the proxy
 * never breaks because of the enhancement layer.
 *
 * Deploy: `npx wrangler deploy` (see README.md for wrangler.toml + deps).
 * Usage:  https://YOUR-WORKER.workers.dev/?url=<enc(photoUrl)>[&center=1]
 * After deploying, set that origin (no trailing `/?url=`) as `imageProxyUrl` in
 * moto2-site's src/config/data-source.ts to route listing media through it.
 */

import {
  PhotonImage,
  SamplingFilter,
  resize,
  crop,
  inc_brightness,
  adjust_contrast,
  saturate_hsl,
  apply_sharpening,
} from "@cf-wasm/photon/workerd";
import { computeCrop } from "./centering.mjs";

/** Enhancement knobs (applied to every photo). Deliberately mild — tune against
 *  real listings via verify-centering.mjs before dialing up. */
const ENHANCE = {
  brightness: 5, // inc_brightness: +N per channel (0–255)
  contrast: 12, // adjust_contrast: mild positive
  saturation: 0.1, // saturate_hsl: +10%
  sharpenAmount: 0.6, // apply_sharpening: unsharp-mask amount
};

/** Long side (px) the analysis thumbnail is downscaled to before the heuristic
 *  runs — keeps bbox detection cheap and a touch denoised. */
const ANALYZE_LONG_SIDE = 200;

/** Output JPEG quality for transformed photos. */
const JPEG_QUALITY = 82;

/** Skip transforming anything larger than this (128MB worker memory cap). */
const MAX_BYTES = 12 * 1024 * 1024;

const ALLOWED_HOSTS = new Set([
  "bdsc.jupiter.ac",
  // ajes.com hero CDN uses numbered subdomains (9.ajes.com etc.) — see check below
]);

function hostAllowed(host) {
  // Numbered-subdomain hero CDNs: N.ajes.com and N.tru.ru (whole-bike shots).
  return ALLOWED_HOSTS.has(host) || /^\d+\.(ajes\.com|tru\.ru)$/.test(host);
}

/**
 * Enhance (and optionally recenter) a photo. Pure with respect to the network:
 * takes encoded bytes, returns encoded JPEG bytes, or throws (caller falls back
 * to the original). `center` gates the auto-centering step only.
 */
function transformPhoto(inputBytes, center) {
  let img = null;
  let thumb = null;
  let cropped = null;
  try {
    img = PhotonImage.new_from_byteslice(inputBytes);
    const fw = img.get_width();
    const fh = img.get_height();

    // --- Auto-center (whole-bike shots): analyze a downscaled copy, then crop
    //     the full-res image to the recenter window.
    let work = img;
    if (center) {
      const scale = Math.min(1, ANALYZE_LONG_SIDE / Math.max(fw, fh));
      const tw = Math.max(1, Math.round(fw * scale));
      const th = Math.max(1, Math.round(fh * scale));
      thumb = resize(img, tw, th, SamplingFilter.Triangle);
      const idata = thumb.get_image_data();
      const decision = computeCrop({ data: idata.data, width: idata.width, height: idata.height });
      if (decision.centered) {
        const c = decision.crop;
        const x1 = Math.max(0, Math.round(c.x * fw));
        const y1 = Math.max(0, Math.round(c.y * fh));
        const x2 = Math.min(fw, Math.round((c.x + c.width) * fw));
        const y2 = Math.min(fh, Math.round((c.y + c.height) * fh));
        if (x2 - x1 >= 8 && y2 - y1 >= 8) {
          cropped = crop(img, x1, y1, x2, y2);
          work = cropped;
        }
      }
    }

    // --- Enhancement (every photo).
    inc_brightness(work, ENHANCE.brightness);
    adjust_contrast(work, ENHANCE.contrast);
    saturate_hsl(work, ENHANCE.saturation);
    apply_sharpening(work, ENHANCE.sharpenAmount, 1.0, 2, 0);

    return work.get_bytes_jpeg(JPEG_QUALITY);
  } finally {
    // Free WASM-side memory regardless of outcome.
    if (thumb) thumb.free();
    if (cropped && cropped !== img) cropped.free();
    if (img) img.free();
  }
}

export default {
  async fetch(request, env, ctx) {
    const reqUrl = new URL(request.url);
    const target = reqUrl.searchParams.get("url");
    if (!target) return new Response("missing ?url=", { status: 400 });
    const wantsCenter = reqUrl.searchParams.get("center") === "1";

    let upstream;
    try {
      upstream = new URL(target);
    } catch {
      return new Response("bad url", { status: 400 });
    }
    if (!hostAllowed(upstream.hostname)) {
      return new Response("host not allowed", { status: 403 });
    }

    // Serve from edge cache when possible. The cache key is the full request
    // URL, so `?center=1` and the plain proxy URL cache independently.
    const cache = caches.default;
    const cacheKey = new Request(reqUrl.toString(), request);
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const resp = await fetch(upstream.toString(), {
      headers: {
        // The fix: look like a koscom page view
        Referer: "https://auc.koscom-trade.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
      },
      cf: { cacheTtl: 86400, cacheEverything: true },
    });

    if (!resp.ok) {
      return new Response(`upstream ${resp.status}`, { status: resp.status });
    }

    const contentType = resp.headers.get("content-type") || "";
    const isImage = contentType.startsWith("image/");

    // Non-images (videos, etc.) stream through untouched, exactly as before.
    if (!isImage) {
      const headers = new Headers(resp.headers);
      headers.set("Access-Control-Allow-Origin", "*");
      headers.set("Cache-Control", "public, max-age=86400");
      const out = new Response(resp.body, { status: 200, headers });
      ctx.waitUntil(cache.put(cacheKey, out.clone()));
      return out;
    }

    // Buffer the image and transform it. Any failure → serve the original bytes.
    const inputBytes = new Uint8Array(await resp.arrayBuffer());
    let outBytes = inputBytes;
    let outType = contentType;
    if (inputBytes.byteLength <= MAX_BYTES) {
      try {
        outBytes = transformPhoto(inputBytes, wantsCenter);
        outType = "image/jpeg";
      } catch (err) {
        // Fall back to the untouched original; log for observability.
        console.error("transform failed, serving original:", err && err.message);
        outBytes = inputBytes;
        outType = contentType;
      }
    }

    const headers = new Headers();
    headers.set("Content-Type", outType);
    headers.set("Access-Control-Allow-Origin", "*");
    headers.set("Cache-Control", "public, max-age=86400");
    const out = new Response(outBytes, { status: 200, headers });
    ctx.waitUntil(cache.put(cacheKey, out.clone()));
    return out;
  },
};
