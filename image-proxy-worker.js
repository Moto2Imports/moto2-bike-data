/**
 * Moto2 Imports — media proxy (Cloudflare Worker)
 *
 * Solves the browser-side hotlink problem: bdsc.jupiter.ac rejects image
 * requests whose Referer isn't a koscom page. This Worker fetches the media
 * server-side with the right Referer and streams it to your widget with
 * open CORS + 24h edge caching.
 *
 * Why Cloudflare Workers instead of Netlify:
 *   - Free tier is 100,000 requests/day, PERMANENT — no promotional credits,
 *     nothing expires, no Git-deploy requirement.
 *   - Built-in edge cache means each photo is fetched from Japan once,
 *     then served from CDN — fast for your US customers.
 *
 * Deploy: dash.cloudflare.com -> Workers -> Create -> paste this -> Deploy.
 * Usage in widget:
 *   https://YOUR-WORKER.workers.dev/?url=<encodeURIComponent(photoUrl)>
 */

const ALLOWED_HOSTS = new Set([
  "bdsc.jupiter.ac",
  // ajes.com hero CDN uses numbered subdomains (9.ajes.com etc.) — see check below
]);

function hostAllowed(host) {
  return ALLOWED_HOSTS.has(host) || /^\d+\.ajes\.com$/.test(host);
}

export default {
  async fetch(request, env, ctx) {
    const reqUrl = new URL(request.url);
    const target = reqUrl.searchParams.get("url");
    if (!target) return new Response("missing ?url=", { status: 400 });

    let upstream;
    try {
      upstream = new URL(target);
    } catch {
      return new Response("bad url", { status: 400 });
    }
    if (!hostAllowed(upstream.hostname)) {
      return new Response("host not allowed", { status: 403 });
    }

    // Serve from edge cache when possible
    const cache = caches.default;
    const cacheKey = new Request(reqUrl.toString(), request);
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const resp = await fetch(upstream.toString(), {
      headers: {
        // The fix: look like a koscom page view
        "Referer": "https://auc.koscom-trade.com/",
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
      },
      cf: { cacheTtl: 86400, cacheEverything: true },
    });

    if (!resp.ok) {
      return new Response(`upstream ${resp.status}`, { status: resp.status });
    }

    const headers = new Headers(resp.headers);
    headers.set("Access-Control-Allow-Origin", "*");
    headers.set("Cache-Control", "public, max-age=86400");
    const out = new Response(resp.body, { status: 200, headers });
    ctx.waitUntil(cache.put(cacheKey, out.clone()));
    return out;
  },
};
