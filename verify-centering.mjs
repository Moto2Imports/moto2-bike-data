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

async function loadBytes(input) {
  if (/^https?:\/\//.test(input)) {
    const r = await fetch(input, { headers: { Referer: REFERER, "User-Agent": UA } });
    if (!r.ok) throw new Error(`fetch ${r.status}`);
    return new Uint8Array(await r.arrayBuffer());
  }
  return new Uint8Array(readFileSync(resolve(input)));
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
    try {
      const bytes = await loadBytes(src);
      img = PhotonImage.new_from_byteslice(bytes);
      const fw = img.get_width();
      const fh = img.get_height();

      const scale = Math.min(1, ANALYZE_LONG_SIDE / Math.max(fw, fh));
      thumb = resize(img, Math.max(1, Math.round(fw * scale)), Math.max(1, Math.round(fh * scale)), SamplingFilter.Triangle);
      const idata = thumb.get_image_data();
      const d = computeCrop({ data: idata.data, width: idata.width, height: idata.height });

      writeFileSync(resolve(out, `${name}-before.jpg`), Buffer.from(img.get_bytes_jpeg(90)));

      const pvScale = Math.min(1, 480 / Math.max(fw, fh));
      const pv = resize(img, Math.max(1, Math.round(fw * pvScale)), Math.max(1, Math.round(fh * pvScale)), SamplingFilter.Triangle);
      const pdata = pv.get_image_data();
      const parr = pdata.data;
      if (d.bbox) drawRect(parr, pdata.width, pdata.height, d.bbox, [40, 220, 90]);
      if (d.crop) drawRect(parr, pdata.width, pdata.height, d.crop, [230, 40, 50]);
      const pvImg = new PhotonImage(parr, pdata.width, pdata.height);
      writeFileSync(resolve(out, `${name}-detect.jpg`), Buffer.from(pvImg.get_bytes_jpeg(88)));
      pvImg.free();
      pv.free();

      let work = img;
      if (d.centered) {
        const c = d.crop;
        cropped = crop(img, Math.round(c.x * fw), Math.round(c.y * fh), Math.round((c.x + c.width) * fw), Math.round((c.y + c.height) * fh));
        work = cropped;
      }
      enhance(work);
      writeFileSync(resolve(out, `${name}-after.jpg`), Buffer.from(work.get_bytes_jpeg(82)));

      rows.push({
        name,
        dims: `${fw}x${fh}`,
        decision: d.centered ? "CENTER" : "skip",
        reason: d.reason,
        fg: d.foregroundFraction.toFixed(3),
        bgMad: d.background.mad.toFixed(1),
      });
    } catch (e) {
      rows.push({ name, dims: "-", decision: "ERROR", reason: e.message, fg: "-", bgMad: "-" });
    } finally {
      if (thumb) thumb.free();
      if (cropped && cropped !== img) cropped.free();
      if (img) img.free();
    }
  }

  console.log("\n  decision  reason           fg     bgMAD  dims        name");
  console.log("  " + "-".repeat(78));
  for (const r of rows) {
    console.log(
      `  ${r.decision.padEnd(8)}  ${r.reason.padEnd(15)}  ${String(r.fg).padStart(5)}  ${String(r.bgMad).padStart(5)}  ${r.dims.padEnd(10)}  ${r.name}`,
    );
  }
  console.log(`\n  wrote before/detect/after JPEGs to ${out}`);
  console.log("  → open the *-detect.jpg (green=bbox, red=recenter window) and *-after.jpg and flag any that look wrong.\n");
}

run();
