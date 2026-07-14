/**
 * Tests for the pure centering heuristic (centering.mjs). Runs under the repo's
 * vitest, no Photon/network needed: synthetic RGBA frames exercise the bbox
 * detection, the recenter math, and every misfire-safety-valve branch.
 */
import { describe, expect, it } from "vitest";
import { computeCrop, estimateBackground } from "./centering.mjs";

/** Build a WxH RGBA frame filled with `bg`, then paint filled `boxes`. */
function frame(w, h, bg, boxes = []) {
  const data = new Uint8Array(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    data[i * 4] = bg[0];
    data[i * 4 + 1] = bg[1];
    data[i * 4 + 2] = bg[2];
    data[i * 4 + 3] = 255;
  }
  for (const { x0, y0, x1, y1, color } of boxes) {
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const i = (y * w + x) * 4;
        data[i] = color[0];
        data[i + 1] = color[1];
        data[i + 2] = color[2];
        data[i + 3] = 255;
      }
    }
  }
  return { data, width: w, height: h };
}

const GRAY = [205, 205, 205];
const DARK = [40, 42, 48];

describe("estimateBackground", () => {
  it("recovers a plain background color with low MAD", () => {
    const f = frame(200, 125, GRAY, [{ x0: 60, y0: 40, x1: 130, y1: 90, color: DARK }]);
    const bg = estimateBackground(f.data, f.width, f.height, 8);
    expect(bg.r).toBe(205);
    expect(bg.g).toBe(205);
    expect(bg.b).toBe(205);
    expect(bg.mad).toBeLessThan(5); // border is uniform → plain
  });
});

describe("computeCrop — centering", () => {
  it("recenters a left-of-center bike onto the frame center", () => {
    // Bike bbox center at x=55/200 = 0.275 (well left of the 0.5 frame center).
    const f = frame(200, 125, GRAY, [{ x0: 20, y0: 40, x1: 89, y1: 89, color: DARK }]);
    const r = computeCrop(f);
    expect(r.centered).toBe(true);
    expect(r.reason).toBe("centered");
    const cropCenterX = r.crop.x + r.crop.width / 2;
    expect(cropCenterX).toBeCloseTo(0.275, 1); // crop follows the bike, not the frame
    expect(cropCenterX).toBeLessThan(0.45); // definitively shifted left of frame center
    // Output crop holds the target 16:10 aspect.
    const aspect = (r.crop.width * f.width) / (r.crop.height * f.height);
    expect(aspect).toBeCloseTo(16 / 10, 1);
    // Detected bbox roughly matches the painted box.
    expect(r.bbox.x).toBeCloseTo(20 / 200, 1);
    expect(r.bbox.width).toBeCloseTo(70 / 200, 1);
  });

  it("leaves an already-centered bike centered", () => {
    const f = frame(200, 125, GRAY, [{ x0: 65, y0: 40, x1: 134, y1: 89, color: DARK }]);
    const r = computeCrop(f);
    expect(r.centered).toBe(true);
    const cropCenterX = r.crop.x + r.crop.width / 2;
    expect(cropCenterX).toBeCloseTo(0.5, 1);
  });

  it("keeps the crop inside the frame (clamps, never negative)", () => {
    // Bike jammed into the top-left corner — centering would push the window
    // off-frame, so it must clamp to origin.
    const f = frame(200, 125, GRAY, [{ x0: 2, y0: 2, x1: 60, y1: 50, color: DARK }]);
    const r = computeCrop(f);
    expect(r.centered).toBe(true);
    expect(r.crop.x).toBeGreaterThanOrEqual(0);
    expect(r.crop.y).toBeGreaterThanOrEqual(0);
    expect(r.crop.x + r.crop.width).toBeLessThanOrEqual(1.0001);
    expect(r.crop.y + r.crop.height).toBeLessThanOrEqual(1.0001);
  });
});

describe("computeCrop — misfire safety valve", () => {
  it("skips a plain frame with no subject (empty)", () => {
    const r = computeCrop(frame(200, 125, GRAY));
    expect(r.centered).toBe(false);
    expect(r.reason).toBe("empty");
    expect(r.crop).toBeNull();
  });

  it("skips a busy / non-plain background", () => {
    // Random-noise border → high MAD → not a studio shot.
    const f = frame(200, 125, GRAY, [{ x0: 60, y0: 40, x1: 130, y1: 90, color: DARK }]);
    for (let y = 0; y < f.height; y++) {
      for (let x = 0; x < f.width; x++) {
        if (x < 8 || y < 8 || x >= f.width - 8 || y >= f.height - 8) {
          const i = (y * f.width + x) * 4;
          const n = (x * 31 + y * 17) % 200; // deterministic pseudo-noise
          f.data[i] = n;
          f.data[i + 1] = 255 - n;
          f.data[i + 2] = (n * 2) % 255;
        }
      }
    }
    const r = computeCrop(f);
    expect(r.centered).toBe(false);
    expect(r.reason).toBe("busy-background");
  });

  it("skips when the subject already fills the frame", () => {
    const f = frame(200, 125, GRAY, [{ x0: 6, y0: 6, x1: 193, y1: 118, color: DARK }]);
    const r = computeCrop(f);
    expect(r.centered).toBe(false);
    expect(["fills-frame", "empty"]).toContain(r.reason);
  });
});
