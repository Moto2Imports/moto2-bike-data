/**
 * verify-centering.mjs — offline harness to eyeball the auto-centering
 * heuristic against REAL listing photos before/after it goes live.
 *
 * It runs the exact same pipeline as the worker (downscale → computeCrop →
 * crop → enhance) but writes visual artifacts you can open and judge:
 *   <name>-before.jpg  the original photo
 *   <name>-detect.jpg  original with the detected bbox (green) + recenter
 *                      window (red) drawn on — shows WHAT the heuristic saw
 *   <name>-after.jpg   the final centered + enhanced output the site would show
 * and prints a table with the decision, reason, foreground fraction and
 * background "plainness" (MAD) for each photo. Anything it SKIPS on a real
 * whole-bike shot — or CENTERS badly — is what you flag back for tuning.
 *
 * Requires: `npm install` (see package.json / IMAGE_PROXY.md).
 *
 * Usage:
 *   node verify-centering.mjs <url|file> [<url|file> ...]
 *   node verify-centering.mjs --listings [N]   # sample N bikes from ./bikes.json
 *   node verify-centering.mjs --out ./verify-out ...
 *
 * URLs are fetched with the koscom Referer (same hotlink bypass the worker
 * uses); bare paths are read from disk. Whole-bike hero shots live on
 * N.tru.ru / N.ajes.com; bdsc.jupiter.ac images are inspection shots (expected
 * to be SKIPPED by the safety valve — a useful negative control).
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { resolve, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";
import {
  PhotonImage,
  SamplingFilter,
  resize,
  crop,
  inc_brightness,
  adjust_contrast,
  saturate_hsl,
  apply_sharpening,
} from "@cf-wasm/photon/node";
import { computeCrop } from "./centering.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ENHANCE = { brightness: 5, contrast: 12, saturation: 0.1, sharpenAmount: 0.6 };
const ANALYZE_LONG_SIDE = 200;
const REFERER = "https://auc.koscom-trade.com/";
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36";

function parseArgs(argv) {
  const inputs = [];
  let out = resolve(HERE, "verify-out");
  let listings = null;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--out") out = resolve(argv[++i]);
    else if (a === "--listings") listings = Number(argv[i + 1]) > 0 ? Number(argv[++i]) : 8;
    else inputs.push(a);
  }
  return { inputs, out, listings };
}

const isHero = (u) => {
  try {
    return /^\d+\.(tru\.ru|ajes\.com)$/.test(new URL(u).hostname);
  } catch {
    return false;
  }
};

/** Pull a spread of real photo URLs from this repo's bikes.json: the first
 *  whole-bike hero shot of N bikes, plus one inspection shot as a negative
 *  control. */
function sampleFromBikes(n) {
  const path = resolve(HERE, "bikes.json");
  const doc = JSON.parse(readFileSync(path, "utf-8"));
  const bikes = Array.isArray(doc) ? doc : doc.bikes || [];
  const step = Math.max(1, Math.floor(bikes.length / n));
  const urls = [];
  for (let i = 0; i < bikes.length && urls.length < n; i += step) {
    const hero = (bikes[i].photos || []).find(isHero);
    if (hero) urls.push(hero);
  }
  for (const b of bikes) {
    const insp = (b.photos || []).find((u) => {
      try {
        return new URL(u).hostname === "bdsc.jupiter.ac";
      } catch {
        return false;
      }
    });
    if (insp) {
      urls.push(insp);
      break;
    }
  }
  return urls;
}

// Browser-parity headers. The hotlink bypass needs `Referer` (same value the
// worker sends); the rest make the request look like a real <img> load, which
// some CDNs also gate on. Node's fetch DOES forward Referer (verified) — so if
// a URL still fails, the printed status/body is the real reason, not a missing
// header.
const FETCH_HEADERS = {
  Referer: REFERER,
  "User-Agent": UA,
  Accept: "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
  "Accept-Language": "en-US,en;q=0.9",
  "Sec-Fetch-Dest": "image",
  "Sec-Fetch-Mode": "no-cors",
  "Sec-Fetch-Site": "cross-site",
};

async function loadBytes(input) {
  if (!/^https?:\/\//.test(input)) {
    return new Uint8Array(readFileSync(resolve(input)));
  }
  let lastErr;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const r = await fetch(input, { headers: FETCH_HEADERS, redirect: "follow" });
      if (!r.ok) {
        const snippet = (await r.text().catch(() => "")).replace(/\s+/g, " ").slice(0, 80);
        throw new Error(`HTTP ${r.status}${snippet ? ` — ${snippet}` : ""}`);
      }
      return new Uint8Array(await r.arrayBuffer());
    } catch (e) {
      lastErr = e;
      if (attempt < 3) await new Promise((res) => setTimeout(res, 300 * attempt));
    }
  }
  throw lastErr;
}

/** Draw a hollow rectangle (fractional coords) onto an RGBA buffer. */
function drawRect(data, w, h, rect, color, thickness = 2) {
  const x0 = Math.max(0, Math.round(rect.x * w));
  const y0 = Math.max(0, Math.round(rect.y * h));
  const x1 = Math.min(w - 1, Math.round((rect.x + rect.width) * w));
  const y1 = Math.min(h - 1, Math.round((rect.y + rect.height) * h));
  const put = (x, y) => {
    if (x < 0 || y < 0 || x >= w || y >= h) return;
    const i = (y * w + x) * 4;
    data[i] = color[0];
    data[i + 1] = color[1];
    data[i + 2] = color[2];
    data[i + 3] = 255;
  };
  for (let t = 0; t < thickness; t++) {
    for (let x = x0; x <= x1; x++) {
      put(x, y0 + t);
      put(x, y1 - t);
    }
    for (let y = y0; y <= y1; y++) {
      put(x0 + t, y);
      put(x1 - t, y);
    }
  }
}

function enhance(img) {
  inc_brightness(img, ENHANCE.brightness);
  adjust_contrast(img, ENHANCE.contrast);
  saturate_hsl(img, ENHANCE.saturation);
  apply_sharpening(img, ENHANCE.sharpenAmount, 1.0, 2, 0);
}

/** Stitch encoded JPEG panels side by side at a common height into one
 *  PhotonImage, so each listing's original | detect | after is viewable in a
 *  single file. Takes ENCODED bytes (not live PhotonImages) and copies each
 *  panel out of WASM memory, avoiding get_image_data() aliasing across the
 *  repeated Photon allocations. */
function montageImages(byteBuffers, H = 260, gap = 8, bg = [26, 26, 28]) {
  const panels = byteBuffers.map((buf) => {
    const im = PhotonImage.new_from_byteslice(new Uint8Array(buf));
    const w = Math.max(1, Math.round((im.get_width() * H) / im.get_height()));
    const r = resize(im, w, H, SamplingFilter.Triangle);
    const d = r.get_image_data();
    const panel = { data: Uint8Array.from(d.data), w: d.width, h: d.height }; // detach from WASM heap
    r.free();
    im.free();
    return panel;
  });
  const totalW = panels.reduce((s, p) => s + p.w, 0) + gap * (panels.length - 1);
  const out = new Uint8Array(totalW * H * 4);
  for (let i = 0; i < totalW * H; i++) {
    out[i * 4] = bg[0];
    out[i * 4 + 1] = bg[1];
    out[i * 4 + 2] = bg[2];
    out[i * 4 + 3] = 255;
  }
  let xoff = 0;
  for (const p of panels) {
    for (let y = 0; y < p.h; y++) {
      for (let x = 0; x < p.w; x++) {
        const si = (y * p.w + x) * 4;
        const di = (y * totalW + (xoff + x)) * 4;
        out[di] = p.data[si];
        out[di + 1] = p.data[si + 1];
        out[di + 2] = p.data[si + 2];
        out[di + 3] = 255;
      }
    }
    xoff += p.w + gap;
  }
  return new PhotonImage(out, totalW, H);
}

async function run() {
  const { inputs, out, listings } = parseArgs(process.argv.slice(2));
  let sources = inputs;
  if (listings) sources = sampleFromBikes(listings);
  if (sources.length === 0) {
    console.error("no inputs. pass urls/files, or --listings [N]");
    process.exit(1);
  }
  if (!existsSync(out)) mkdirSync(out, { recursive: true });

  const rows = [];
  for (let idx = 0; idx < sources.length; idx++) {
    const src = sources[idx];
    const name = `${String(idx).padStart(2, "0")}-${basename(src).slice(0, 24).replace(/[^\w.-]/g, "_")}`;
    let img = null;
    let thumb = null;
    let cropped = null;
    let detectPanel = null;
    let montage = null;
    try {
      const bytes = await loadBytes(src);
      img = PhotonImage.new_from_byteslice(bytes);
      const fw = img.get_width();
      const fh = img.get_height();

      const scale = Math.min(1, ANALYZE_LONG_SIDE / Math.max(fw, fh));
      thumb = resize(img, Math.max(1, Math.round(fw * scale)), Math.max(1, Math.round(fh * scale)), SamplingFilter.Triangle);
      const idata = thumb.get_image_data();
      const d = computeCrop({ data: idata.data, width: idata.width, height: idata.height });

      // The ORIGINAL, always written for every item (incl. skips) so you can
      // judge whether "skip" was the right call rather than trust the label.
      const origBytes = Buffer.from(img.get_bytes_jpeg(90));
      writeFileSync(resolve(out, `${name}-original.jpg`), origBytes);

      // Detection overlay — green = detected bbox, red = recenter window. Both
      // are drawn on skips too (bbox is reported on every decision now).
      const pvScale = Math.min(1, 480 / Math.max(fw, fh));
      const pw = Math.max(1, Math.round(fw * pvScale));
      const ph = Math.max(1, Math.round(fh * pvScale));
      const dpv = resize(img, pw, ph, SamplingFilter.Triangle);
      const pdata = dpv.get_image_data();
      const parr = Uint8Array.from(pdata.data); // copy out of WASM heap before drawing
      if (d.bbox) drawRect(parr, pdata.width, pdata.height, d.bbox, [40, 220, 90]);
      if (d.crop) drawRect(parr, pdata.width, pdata.height, d.crop, [230, 40, 50]);
      detectPanel = new PhotonImage(parr, pdata.width, pdata.height);
      dpv.free();
      const detBytes = Buffer.from(detectPanel.get_bytes_jpeg(88));
      writeFileSync(resolve(out, `${name}-detect.jpg`), detBytes);

      // Final output: cropped (if centered) then enhanced.
      let work = img;
      if (d.centered) {
        const c = d.crop;
        cropped = crop(img, Math.round(c.x * fw), Math.round(c.y * fh), Math.round((c.x + c.width) * fw), Math.round((c.y + c.height) * fh));
        work = cropped;
      }
      enhance(work);
      const afterBytes = Buffer.from(work.get_bytes_jpeg(82));
      writeFileSync(resolve(out, `${name}-after.jpg`), afterBytes);

      // One-glance montage: original | detect | after (built from encoded bytes).
      montage = montageImages([origBytes, detBytes, afterBytes]);
      writeFileSync(resolve(out, `${name}-montage.jpg`), Buffer.from(montage.get_bytes_jpeg(86)));

      rows.push({
        name,
        dims: `${fw}x${fh}`,
        decision: d.centered ? "CENTER" : "skip",
        reason: d.reason,
        detail: d.detail || "",
        fg: d.foregroundFraction.toFixed(3),
        bgMad: d.background.mad.toFixed(1),
      });
    } catch (e) {
      rows.push({ name, dims: "-", decision: "ERROR", reason: "fetch/decode", detail: e.message, fg: "-", bgMad: "-" });
    } finally {
      if (thumb) thumb.free();
      if (cropped && cropped !== img) cropped.free();
      if (detectPanel) detectPanel.free();
      if (montage) montage.free();
      if (img) img.free();
    }
  }

  console.log("\n  decision  reason           fg      bgMAD  dims        name");
  console.log("  " + "-".repeat(84));
  for (const r of rows) {
    console.log(
      `  ${r.decision.padEnd(8)}  ${r.reason.padEnd(15)}  ${String(r.fg).padStart(5)}  ${String(r.bgMad).padStart(5)}  ${r.dims.padEnd(10)}  ${r.name}`,
    );
    if (r.detail) console.log(`  ${" ".repeat(8)}  └ ${r.detail}`);
  }
  console.log(`\n  wrote original/detect/after/montage JPEGs to ${out}`);
  console.log("  → open each *-montage.jpg (original | green=bbox,red=window | result) and flag any skip/center that looks wrong.\n");
}

run();
