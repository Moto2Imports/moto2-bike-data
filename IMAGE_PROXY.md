# Media proxy worker — enhancement + auto-centering

`image-proxy-worker.js` is the Cloudflare Worker that proxies auction media
(hotlink bypass) **plus** two photo features:

1. **Enhancement** — a mild brightness / contrast / saturation / sharpen pass on
   **every** photo.
2. **Auto-centering** — for whole-bike hero shots only (requests with
   `?center=1`), a cheap color heuristic finds the bike against the plain studio
   background and recenters the frame on it. No AI model.

## Files (all at repo root)

| File | Role |
|---|---|
| `image-proxy-worker.js` | The Worker: proxy + enhance every photo + recenter when `?center=1`. |
| `centering.mjs` | Pure, dependency-free centering heuristic (shared by the worker + harness). |
| `centering.test.mjs` | Unit tests for the heuristic (`npm test`). |
| `verify-centering.mjs` | Offline harness — before/after visuals on real photos. |
| `wrangler.toml` | Deploy config. |
| `package.json` | Worker + tooling deps. |

The `?center=1` flag is set by **moto2-site** (adapter `isWholeBikeShot`) only on
the leading whole-bike hero shots (`N.tru.ru` / `N.ajes.com`), before the
inspection grid. Inspection/accessory shots are never flagged. Enhancement is
always-on and needs no flag.

## The centering heuristic (and its misfire safety valve)

1. Estimate the studio background from a thin border ring (median color + a
   "plainness" measure).
2. Mark pixels far from that background as foreground; take the occupied
   row/column span as the bike's bounding box.
3. Pad it, grow to the ~16:10 card aspect, and center the crop window on the box.

It **declines to center** (enhances but does not crop) whenever it isn't
confident — busy/non-plain background, foreground fills the frame, nothing
detected, or the crop would exceed the frame. So a stray inspection/accessory
shot that slips through with `center=1` is left alone rather than mis-cropped.
Knobs live in `DEFAULTS` in `centering.mjs`; expect one tuning pass on real
photos (below) before trusting the thresholds.

## Deploy

```bash
npm install                # @cf-wasm/photon (+ wrangler)
npx wrangler deploy        # needs Workers Paid — see wrangler.toml PLAN NOTE
```

Then point the site at it — in `moto2-site/src/config/data-source.ts`:

```ts
imageProxyUrl: "https://moto2-image-proxy.<your-subdomain>.workers.dev",
```

…and re-run moto2-site's data refresh so listing URLs route through the proxy
(whole-bike shots pick up `&center=1` automatically).

**Plan note:** the transform decodes images in-worker → needs Workers **Paid**
(the free plan's 10 ms CPU limit is too low). If a transform fails or the image
is oversized, the worker streams the original bytes — the proxy never breaks.

## Verify centering against real listings

Because the transform can misfire on an unusually busy or non-standard
background, eyeball it on real photos before going live:

```bash
npm install
# Sample whole-bike hero shots (+ one inspection shot as a negative control)
# straight from this repo's bikes.json:
node verify-centering.mjs --listings 8

# …or pass explicit image URLs / local files:
node verify-centering.mjs "https://7.tru.ru/imgs/…" ./some-photo.jpg
```

For each photo (including skips — the original is always written so you can
judge the call yourself) it writes to `verify-out/`:

- `*-original.jpg` — the untouched original
- `*-detect.jpg` — original with **green** = detected bbox, **red** = recenter window
- `*-after.jpg` — the final result (centered + enhanced, or enhance-only on a skip)
- `*-montage.jpg` — original ∣ detect ∣ after in one image, for quick eyeballing

and prints a table with `decision`, `reason`, a human-readable `detail`, the
measured `foreground` fraction, and background `MAD` (border "plainness"). The
foreground fraction is a real measurement on every row — including skips — not a
placeholder. Open each `*-montage.jpg` and **flag any where centering looks
wrong** — those are candidates to special-case (drop the `center` flag for that
listing in moto2-site) or to tune the `DEFAULTS` thresholds.

Skip reasons: `busy-background` (border not plain → bg estimate untrustworthy),
`empty` (almost no foreground), `fills-frame` (subject already spans the frame —
either coverage > 90%, or the padded/target-aspect crop wouldn't fit), and
`degenerate`.

> Fetching the real hosts (`*.tru.ru`, `*.ajes.com`, `bdsc.jupiter.ac`) needs
> network egress to them; run this where that egress is allowed.

## Test

```bash
npm test          # runs centering.test.mjs
```

## Tuning knobs

- **Enhancement** — `ENHANCE` in `image-proxy-worker.js`.
- **Centering** — `DEFAULTS` in `centering.mjs` (`fgTolerance`, `maxBorderMad`,
  `minForeground`/`maxForeground`, `padFrac`, `targetAspect`).
