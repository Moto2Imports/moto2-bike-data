/**
 * centering.mjs — pure, dependency-free bike auto-centering heuristic.
 *
 * Shared by the Cloudflare image-proxy worker (image-proxy-worker.js) and the
 * offline verification harness (verify-centering.mjs) so both run the EXACT
 * same logic. It takes a frame of raw RGBA pixels and returns a recenter crop
 * rectangle (as fractions of the frame) plus the reasoning, or a decision to
 * leave the image alone.
 *
 * Approach (cheap color heuristic, NOT an AI model, per the brief):
 *   1. Estimate the studio background color from a thin border ring, using the
 *      median (robust to a floor shadow) + median-abs-deviation (a "is the
 *      background actually plain?" confidence check).
 *   2. Mark pixels whose color is far enough from the background as foreground.
 *   3. Project foreground onto rows/cols and take the occupied span as the
 *      bike's bounding box (projection + a coverage threshold rejects specks and
 *      stray dust far better than a raw min/max).
 *   4. Pad the bbox, grow it to the card aspect ratio, and center it on the
 *      bbox center — that recenters the frame around the bike.
 *
 * Misfire safety valve — return { centered:false } (worker then enhances but
 * does NOT crop) whenever the heuristic is not confident:
 *   - border is not plain (high MAD)         → busy / non-standard background
 *   - foreground fills the frame (> maxFg)    → bg estimate failed / busy photo
 *   - almost no foreground (< minFg)          → nothing detected
 *   - the desired crop would exceed the frame → bike already fills it
 * This is what protects any inspection/accessory shot that slips through with a
 * busy background: when in doubt, do nothing.
 */

/** Tunable knobs. RGB distances are 0–255; fractions are of the analyzed frame.
 *  Defaults are conservative starting points — expect one tuning pass against
 *  real listing photos via verify-centering.mjs before trusting them in prod. */
export const DEFAULTS = {
  /** Long-side px the caller should downscale to before analysis (speed +
   *  denoise). Documented here; the worker/harness do the resize. */
  analyzeLongSide: 200,
  /** Border ring width sampled for the background estimate, as a frac of the
   *  short side. */
  borderFrac: 0.06,
  /** Color distance (Euclidean RGB) beyond which a pixel is "foreground". */
  fgTolerance: 34,
  /** A row/col counts as occupied when this fraction of it is foreground. */
  occupancyFrac: 0.04,
  /** Margin added around the detected bbox, as a fraction of bbox size. */
  padFrac: 0.12,
  /** Output crop aspect (matches the ~16:10 listing card, BUILD_BRIEF §5.2). */
  targetAspect: 16 / 10,
  /** Below this foreground fraction → nothing detected → skip. */
  minForeground: 0.015,
  /** Above this foreground fraction → bg failed / busy image → skip. */
  maxForeground: 0.9,
  /** Border median-abs-deviation above which the background is "not plain". */
  maxBorderMad: 22,
};

/** Euclidean RGB distance between a pixel at offset i and an {r,g,b} color. */
function dist(data, i, c) {
  const dr = data[i] - c.r;
  const dg = data[i + 1] - c.g;
  const db = data[i + 2] - c.b;
  return Math.sqrt(dr * dr + dg * dg + db * db);
}

function median(arr) {
  if (arr.length === 0) return 0;
  const a = arr.slice().sort((x, y) => x - y);
  const m = a.length >> 1;
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

/**
 * Estimate background color + "plainness" from the border ring.
 * Returns { r, g, b, mad } where mad is the median absolute deviation of each
 * sampled pixel's distance from the median color (low = uniform = plain).
 */
export function estimateBackground(data, w, h, border) {
  const rs = [];
  const gs = [];
  const bs = [];
  const isBorder = (x, y) => x < border || y < border || x >= w - border || y >= h - border;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (!isBorder(x, y)) continue;
      const i = (y * w + x) * 4;
      rs.push(data[i]);
      gs.push(data[i + 1]);
      bs.push(data[i + 2]);
    }
  }
  const c = { r: median(rs), g: median(gs), b: median(bs) };
  const devs = [];
  for (let k = 0; k < rs.length; k++) {
    const dr = rs[k] - c.r;
    const dg = gs[k] - c.g;
    const db = bs[k] - c.b;
    devs.push(Math.sqrt(dr * dr + dg * dg + db * db));
  }
  return { ...c, mad: median(devs) };
}

/**
 * Compute a recenter crop for a frame of RGBA pixels.
 *
 * @param {{data: Uint8Array|Uint8ClampedArray, width: number, height: number}} frame
 * @param {object} [opts] overrides for DEFAULTS
 * @returns {{
 *   centered: boolean,
 *   reason: string,
 *   crop: {x:number,y:number,width:number,height:number}|null, // fractions 0..1
 *   bbox: {x:number,y:number,width:number,height:number}|null,  // fractions 0..1
 *   background: {r:number,g:number,b:number,mad:number},
 *   foregroundFraction: number
 * }}
 */
export function computeCrop(frame, opts = {}) {
  const o = { ...DEFAULTS, ...opts };
  const { data, width: w, height: h } = frame;
  const border = Math.max(1, Math.round(Math.min(w, h) * o.borderFrac));
  const bg = estimateBackground(data, w, h, border);

  // Always measure foreground first, so EVERY decision (including the skips)
  // reports a real foreground fraction and bbox — never a placeholder 0.000.
  // (Note: when the border isn't plain, `bg` is an unreliable reference, so a
  // busy-background photo's fraction is noisy; it's reported for insight, not
  // trusted for the crop.)
  const rowCount = new Array(h).fill(0);
  const colCount = new Array(w).fill(0);
  let fg = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (dist(data, i, bg) > o.fgTolerance) {
        rowCount[y]++;
        colCount[x]++;
        fg++;
      }
    }
  }
  const frac = fg / (w * h);

  // Occupied span = first/last row/col above the coverage threshold → bbox.
  const rowThresh = o.occupancyFrac * w;
  const colThresh = o.occupancyFrac * h;
  const first = (arr, t) => {
    for (let i = 0; i < arr.length; i++) if (arr[i] >= t) return i;
    return -1;
  };
  const last = (arr, t) => {
    for (let i = arr.length - 1; i >= 0; i--) if (arr[i] >= t) return i;
    return -1;
  };
  const y0 = first(rowCount, rowThresh);
  const y1 = last(rowCount, rowThresh);
  const x0 = first(colCount, colThresh);
  const x1 = last(colCount, colThresh);
  const degenerate = x0 < 0 || y0 < 0 || x1 <= x0 || y1 <= y0;
  const bboxFrac = degenerate
    ? null
    : { x: x0 / w, y: y0 / h, width: (x1 - x0 + 1) / w, height: (y1 - y0 + 1) / h };

  // Common result shell — carries the measured fraction + bbox on every path.
  const result = (centered, reason, detail, crop = null) => ({
    centered,
    reason,
    detail,
    crop,
    bbox: bboxFrac,
    background: bg,
    foregroundFraction: frac,
  });

  // --- Decisions (order matters). Thresholds unchanged; each explains itself.
  if (bg.mad > o.maxBorderMad) {
    // Border ring isn't uniform → not a plain studio backdrop → bg estimate
    // can't be trusted. A safety-valve DECISION (not "zero foreground found").
    return result(false, "busy-background", `borderMAD ${bg.mad.toFixed(1)} > ${o.maxBorderMad}`);
  }
  if (frac < o.minForeground) {
    return result(false, "empty", `foreground ${frac.toFixed(3)} < ${o.minForeground}`);
  }
  if (frac > o.maxForeground) {
    return result(false, "fills-frame", `coverage ${frac.toFixed(3)} > ${o.maxForeground}`);
  }
  if (degenerate) {
    return result(false, "degenerate", "no row/col span above occupancy threshold");
  }

  // Pad the bbox (in pixel space), then grow to the target aspect.
  const bw = x1 - x0 + 1;
  const bh = y1 - y0 + 1;
  const cx = (x0 + x1 + 1) / 2;
  const cy = (y0 + y1 + 1) / 2;
  let cw = bw * (1 + 2 * o.padFrac);
  let ch = bh * (1 + 2 * o.padFrac);
  if (cw / ch < o.targetAspect) cw = ch * o.targetAspect;
  else ch = cw / o.targetAspect;

  // The padded, aspect-fitted crop exceeds the frame → the bike already spans
  // it, so no smaller centered crop of the target aspect fits. Leave it alone.
  if (cw > w || ch > h) {
    const over = [cw > w ? "width" : null, ch > h ? "height" : null].filter(Boolean).join("+");
    return result(false, "fills-frame", `crop ${Math.round(cw)}x${Math.round(ch)} exceeds ${w}x${h} (${over})`);
  }

  // Center on the bbox center, then clamp the window inside the frame.
  let rx = cx - cw / 2;
  let ry = cy - ch / 2;
  rx = Math.max(0, Math.min(w - cw, rx));
  ry = Math.max(0, Math.min(h - ch, ry));

  return result(
    true,
    "centered",
    `shifted ~${(Math.abs(0.5 - (rx + cw / 2) / w) * 100).toFixed(0)}% from frame center`,
    { x: rx / w, y: ry / h, width: cw / w, height: ch / h },
  );
}
