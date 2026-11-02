import { mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "playwright-core";

const base = process.argv[2] ?? "http://localhost:5173";
const out = process.argv[3] ?? "../../data/tmp/map-time";
const only = process.argv[4] ?? "";
mkdirSync(out, { recursive: true });

const CASES = [
  { name: "us", path: "/map/us/2024-11-05-general/president" },
  { name: "us-georgia", path: "/map/us/2024-11-05-general/president/georgia" },
  { name: "gb", path: "/map/gb/2024-07-04-general/house-of-commons" },
  { name: "de", path: "/map/de/2025-02-23-general/bundestag" },
  { name: "tr", path: "/map/tr/2023-05-14-general/president" },
];
const WIDTHS = [
  { name: "1440", width: 1440, height: 900, isMobile: false },
  { name: "390", width: 390, height: 844, isMobile: true },
];
const QUIET = 6000;
const LIMIT = 60_000;

const browser = await chromium.launch({
  executablePath: "/usr/bin/chromium-browser",
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});

const sample = (page) =>
  page.evaluate(() => {
    const canvas = document.querySelector("[data-view] canvas, canvas");
    if (canvas == null) {
      return { colors: 0, hash: 0 };
    }
    const probe = document.createElement("canvas");
    probe.width = 64;
    probe.height = 64;
    const pen = probe.getContext("2d");
    pen.drawImage(canvas, 0, 0, 64, 64);
    const data = pen.getImageData(0, 0, 64, 64).data;
    const seen = new Set();
    let hash = 0;
    for (let i = 0; i < data.length; i += 4) {
      seen.add(`${data[i] >> 3},${data[i + 1] >> 3},${data[i + 2] >> 3}`);
      hash = Math.trunc(
        (hash * 31 + data[i] + data[i + 1] * 7 + data[i + 2] * 13) % 2 ** 31,
      );
    }
    return { colors: seen.size, hash };
  });

const results = [];
for (const test of CASES) {
  if (!test.name.includes(only)) {
    continue;
  }
  for (const size of WIDTHS) {
    const context = await browser.newContext({
      viewport: { width: size.width, height: size.height },
      isMobile: size.isMobile,
      hasTouch: size.isMobile,
    });
    await context.addInitScript(() => {
      performance.setResourceTimingBufferSize(100_000);
      Object.defineProperty(globalThis, "__long", { value: [] });
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          globalThis.__long.push(entry.duration);
        }
      }).observe({ type: "longtask", buffered: true });
    });
    const page = await context.newPage();
    let busy = 0;
    let lastBusy = 0;
    page.on("request", () => {
      busy += 1;
    });
    for (const event of ["requestfinished", "requestfailed"]) {
      page.on(event, () => {
        busy -= 1;
        lastBusy = Date.now();
      });
    }
    const start = Date.now();
    try {
      await page.goto(`${base}${test.path}`, {
        waitUntil: "domcontentloaded",
        timeout: LIMIT,
      });
      let first = null;
      let settled = null;
      let last = null;
      while (Date.now() - start < LIMIT) {
        const now = await sample(page).catch(() => ({ colors: 0, hash: 0 }));
        const at = Date.now() - start;
        if (first == null && now.colors > 3) {
          first = at;
        }
        if (now.colors > 3 && now.hash !== last) {
          settled = at;
          last = now.hash;
        }
        if (
          settled != null &&
          at - settled > QUIET &&
          busy <= 0 &&
          Date.now() - lastBusy > QUIET
        ) {
          break;
        }
        await page.waitForTimeout(100);
      }
      const network = await page.evaluate(() => {
        const kinds = {};
        for (const entry of performance.getEntriesByType("resource")) {
          const url = new URL(entry.name);
          const kind = /\.pmtiles$|\/tiles\//u.test(url.pathname)
            ? "tiles"
            : /\/(rpc|api)\//u.test(url.pathname)
              ? "data"
              : entry.initiatorType === "script" ||
                  /\.[jt]s$/u.test(url.pathname)
                ? "script"
                : "other";
          kinds[kind] = (kinds[kind] ?? 0) + entry.transferSize;
        }
        const [nav] = performance.getEntriesByType("navigation");
        kinds.document = nav?.transferSize ?? 0;
        return kinds;
      });
      const long = await page.evaluate(() => globalThis.__long);
      let longTotal = 0;
      for (const value of long) {
        longTotal += value;
      }
      const row = {
        label: `${test.name}-${size.name}`,
        firstColor: first,
        settled,
        longTasks: long.length,
        longTotal: Math.round(longTotal),
        longMax: Math.round(Math.max(0, ...long)),
        kilobytes: Object.fromEntries(
          Object.entries(network).map(([kind, bytes]) => [
            kind,
            Math.round(bytes / 1024),
          ]),
        ),
      };
      await page.screenshot({ path: `${out}/${row.label}.png` });
      console.log(JSON.stringify(row));
      results.push(row);
    } catch (error) {
      console.log(
        JSON.stringify({
          label: `${test.name}-${size.name}`,
          error: String(error).slice(0, 200),
        }),
      );
    }
    await context.close();
  }
}

await browser.close();
writeFileSync(`${out}/results.json`, JSON.stringify(results, null, 2));
