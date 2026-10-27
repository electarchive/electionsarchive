import { mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "playwright-core";

const base = process.argv[2] ?? "http://localhost:5173";
const out = process.argv[3] ?? "../../data/tmp/map-qa";
const only = process.argv[4] ?? "";
mkdirSync(out, { recursive: true });

const CASES = [
  {
    name: "us-full",
    path: "/map/us/2024-11-05-general/president",
    isFull: true,
    drill: { unit: "georgia", up: "United States" },
  },
  {
    name: "us-full-georgia",
    path: "/map/us/2024-11-05-general/president/georgia",
    isFull: true,
    drill: { unit: "georgia", up: "United States" },
  },
  {
    name: "us-full-swing",
    path: "/map/us/2024-11-05-general/president?vs=us/2020-11-03-general/president&color=swing",
    isFull: true,
    drill: { unit: "georgia", up: "United States" },
  },
  { name: "us-contest", path: "/us/2024-11-05-general/president" },
  {
    name: "us-contest-alaska",
    path: "/us/2024-11-05-general/president/alaska",
  },
  {
    name: "us-contest-compare",
    path: "/us/2024-11-05-general/president?vs=us/2020-11-03-general/president&shade=swing",
  },
  {
    name: "de-full",
    path: "/map/de/2025-02-23-general/bundestag",
    isFull: true,
    drill: { unit: "bayern", up: "Germany" },
  },
  { name: "fr-contest", path: "/fr/2022-04-10-general/president" },
  { name: "gb-hub", path: "/gb/2024-07-04-general", toggle: "Map" },
  { name: "us-house", path: "/us/ga/2024-11-05-general/house-5" },
  {
    name: "tr-full",
    path: "/map/tr/2023-05-14-general/president",
    isFull: true,
  },
];
const WIDTHS = [
  { name: "1440", width: 1440, height: 900, isMobile: false },
  { name: "390", width: 390, height: 844, isMobile: true },
];
const SLACK = 1e-6;

const results = [];
const browser = await chromium.launch({
  executablePath: "/usr/bin/chromium-browser",
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});

const view = (page) =>
  page.evaluate(() => {
    const host = document.querySelector("[data-view]");
    return host == null ? null : JSON.parse(host.dataset.view ?? "null");
  });

const settle = async (page) => {
  await page.waitForTimeout(250);
  await page
    .waitForFunction(
      () => {
        const host = document.querySelector("[data-view]");
        return host != null && !host.matches(".opacity-0");
      },
      null,
      { timeout: 60_000 },
    )
    .catch(() => {});
  await page.waitForTimeout(600);
};

const inks = (page) =>
  page.evaluate(() => {
    const canvas = document.querySelector("[data-view] canvas");
    if (canvas == null) {
      return 0;
    }
    const probe = document.createElement("canvas");
    probe.width = 24;
    probe.height = 24;
    const pen = probe.getContext("2d");
    pen.drawImage(canvas, 0, 0, 24, 24);
    const data = pen.getImageData(0, 0, 24, 24).data;
    const seen = new Set();
    for (let i = 0; i < data.length; i += 4) {
      seen.add(`${data[i] >> 3},${data[i + 1] >> 3},${data[i + 2] >> 3}`);
    }
    return seen.size;
  });

const inside = (inner, outer) =>
  inner.west >= outer.west - SLACK &&
  inner.east <= outer.east + SLACK &&
  inner.south >= outer.south - SLACK &&
  inner.north <= outer.north + SLACK;

const picked = only.startsWith("/")
  ? [
      {
        name: only.replaceAll(/[^a-z0-9]+/giu, "-").slice(1, 60),
        path: only,
        isFull: only.startsWith("/map/"),
      },
    ]
  : CASES.filter((entry) => entry.name.includes(only));
for (const test of picked) {
  for (const size of WIDTHS) {
    for (const theme of ["light", "dark"]) {
      const label = `${test.name}-${size.name}-${theme}`;
      const failures = [];
      const context = await browser.newContext({
        viewport: { width: size.width, height: size.height },
        isMobile: size.isMobile,
        hasTouch: size.isMobile,
        colorScheme: theme,
      });
      await context.addInitScript((mode) => {
        if (location.protocol.startsWith("http")) {
          localStorage.setItem("mode-watcher-mode", mode);
        }
        addEventListener("DOMContentLoaded", () => {
          const style = document.createElement("style");
          style.textContent =
            ".tsqd-parent-container { display: none !important; }";
          document.head.append(style);
        });
      }, theme);
      const page = await context.newPage();
      page.on("pageerror", (error) => {
        if (!/@vite\/client/u.test(error.stack ?? "")) {
          failures.push(`page error: ${error.message}`);
        }
      });
      page.on("console", (message) => {
        if (
          message.type() === "error" &&
          !/Failed to load resource/u.test(message.text())
        ) {
          failures.push(`console: ${message.text().slice(0, 200)}`);
        }
      });
      const check = (isOk, message) => {
        if (!isOk) {
          failures.push(message);
        }
      };
      try {
        await page.goto(`${base}${test.path}`, {
          waitUntil: "domcontentloaded",
          timeout: 120_000,
        });
        if (test.toggle != null) {
          await page
            .getByRole("button", { name: test.toggle, exact: true })
            .first()
            .click({ timeout: 120_000 });
        }
        await page.waitForSelector("[data-view]", { timeout: 120_000 });
        const host = page.locator("[data-view]").first();
        await host.scrollIntoViewIfNeeded();
        await settle(page);
        const home = await view(page);
        check(home != null, "no view published");
        if (home == null) {
          throw new Error("no map");
        }
        check(inside(home.frame, home), "frame not fully visible on load");
        check(
          home.limit != null && inside(home, home.limit),
          "view outside its limit on load",
        );
        check(
          Math.abs(home.zoom - home.min) < 0.01,
          `not at the fit zoom on load (${home.zoom} vs ${home.min})`,
        );
        check((await inks(page)) > 3, "map blank on load");
        await page.screenshot({ path: `${out}/${label}-load.png` });

        const box = await host.boundingBox();
        const middle = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
        const zoomOutButton = page
          .getByRole("button", { name: /zoom out/i })
          .first();
        const zoomInButton = page
          .getByRole("button", { name: /zoom in/i })
          .first();
        const hasButtons = await zoomInButton.isVisible();
        const zoom = async (button, delta) => {
          if (hasButtons) {
            await button.click();
          } else {
            await page.mouse.move(middle.x, middle.y);
            await page.mouse.wheel(0, delta);
            await page.waitForTimeout(120);
          }
        };
        const zoomOut = { click: () => zoom(zoomOutButton, 600) };
        const zoomIn = { click: () => zoom(zoomInButton, -600) };
        for (let step = 0; step < 6; step += 1) {
          await zoomOut.click();
        }
        await settle(page);
        const outer = await view(page);
        check(
          outer.zoom >= home.min - 0.01,
          `zoomed out past the polity (${outer.zoom} < ${home.min})`,
        );
        check(
          inside(outer, outer.limit),
          "zooming out showed beyond the polity frame",
        );

        for (let step = 0; step < 4; step += 1) {
          await zoomIn.click();
        }
        await settle(page);
        for (const [dx, dy, name] of [
          [1, 0, "east"],
          [-1, 0, "west"],
          [0, 1, "south"],
          [0, -1, "north"],
          [1, 1, "southeast"],
          [-1, 1, "southwest"],
          [1, -1, "northeast"],
          [-1, -1, "northwest"],
        ]) {
          for (let drag = 0; drag < 3; drag += 1) {
            await page.mouse.move(middle.x, middle.y);
            await page.mouse.down();
            await page.mouse.move(
              middle.x - dx * box.width * 0.45,
              middle.y - dy * box.height * 0.45,
              { steps: 6 },
            );
            await page.mouse.up();
          }
          await settle(page);
          const panned = await view(page);
          check(
            inside(panned, panned.limit),
            `panning ${name} left the polity frame`,
          );
          if (name === "west") {
            await page.screenshot({ path: `${out}/${label}-pan-west.png` });
          }
        }

        for (let step = 0; step < 12; step += 1) {
          await zoomIn.click();
        }
        await settle(page);
        const deep = await view(page);
        check(inside(deep, deep.limit), "fully zoomed in view left the frame");

        const resetButton = page
          .getByRole("button", { name: /reset|whole map/i })
          .first();
        if (await resetButton.isVisible()) {
          await resetButton.click();
          await page.waitForTimeout(1200);
          await settle(page);
          const reset = await view(page);
          check(
            Math.abs(reset.zoom - home.min) < 0.05,
            "reset did not return to the fit zoom",
          );
          check((await inks(page)) > 3, "map blank after reset");
        } else {
          for (let step = 0; step < 20; step += 1) {
            await zoomOut.click();
          }
          await settle(page);
          const reset = await view(page);
          check(
            Math.abs(reset.zoom - home.min) < 0.05,
            "zooming fully out did not return to the fit zoom",
          );
        }

        await page.mouse.move(middle.x, middle.y);
        await page.waitForTimeout(400);
        const card = await page
          .locator("[aria-live=polite], .shadow-menu")
          .filter({ hasText: /\S/ })
          .count();
        check(
          card > 0 || size.isMobile,
          "no hover card over the middle of the map",
        );
        const unpinned = page.url();
        await page.mouse.click(middle.x + 3, middle.y + 3);
        await page.waitForTimeout(test.isFull ? 1500 : 300);
        if (test.isFull) {
          check(
            await page
              .getByText(/(?:^|, )[\d,]+ votes$/u)
              .first()
              .waitFor({ timeout: 5000 })
              .then(() => true)
              .catch(() => false),
            "clicking a place did not open its card",
          );
          const alone = page
            .getByRole("button", { name: /^Map .+ alone$/u })
            .first();
          const canDrill = test.drill != null || (await alone.isVisible());
          let [from, to] = [unpinned, null];
          if (canDrill) {
            if (await alone.isVisible()) {
              await alone.click();
            } else {
              const [bare, query = ""] = test.path.split("?");
              const deep = `${bare}/${test.drill.unit}${query === "" ? "" : `?${query}`}`;
              if (!bare.endsWith(`/${test.drill.unit}`)) {
                await page.goto(`${base}${deep}`, {
                  waitUntil: "domcontentloaded",
                });
                await page.waitForSelector("[data-view]", { timeout: 120_000 });
                await settle(page);
              }
              from = page.url();
              await page
                .getByRole("link", { name: test.drill.up, exact: true })
                .first()
                .click();
            }
            await page.waitForTimeout(2500);
            await settle(page);
            to = page.url();
            check(to !== from, "drilling did not change the URL");
            const inner = await view(page);
            check(
              inner != null && inside(inner, inner.limit),
              "after drilling the view left the frame",
            );
            check(
              inner != null && Math.abs(inner.zoom - inner.min) < 0.05,
              "after drilling the map is not at the fit zoom",
            );
            await page.screenshot({ path: `${out}/${label}-drilled.png` });
            await page.goBack();
            await page.waitForTimeout(2500);
            await settle(page);
            check(
              page.url() === from,
              "back did not return to the previous map",
            );
            const outer = await view(page);
            check(
              outer != null && Math.abs(outer.zoom - outer.min) < 0.05,
              "after going back the map is not at the fit zoom",
            );
            await page.goForward();
            await page.waitForTimeout(2500);
            await settle(page);
            check(
              page.url() === to,
              "forward did not return to the drilled map",
            );
            check((await inks(page)) > 3, "map blank after back and forward");
            await page.goto(`${base}${test.path}`, {
              waitUntil: "domcontentloaded",
            });
            if (test.toggle != null) {
              await page
                .getByRole("button", { name: test.toggle, exact: true })
                .first()
                .click({ timeout: 120_000 });
            }
            await page.waitForSelector("[data-view]", { timeout: 120_000 });
            await settle(page);
          }
        }
        if (!test.isFull) {
          await page.goto(`${base}${test.path}`, {
            waitUntil: "domcontentloaded",
          });
          if (test.toggle != null) {
            await page
              .getByRole("button", { name: test.toggle, exact: true })
              .first()
              .click({ timeout: 120_000 });
          }
          await page.waitForSelector("[data-view]", { timeout: 120_000 });
          await settle(page);
        }
        await page.setViewportSize({ width: 390, height: 844 });
        await settle(page);
        const narrow = await view(page);
        check(
          narrow != null && inside(narrow, narrow.limit),
          "after resize to 390 the view left the frame",
        );
        check(
          narrow != null && inside(narrow.frame, narrow.limit),
          "after resize to 390 the frame no longer fits the limit",
        );
        await page.setViewportSize({ width: size.width, height: size.height });
        await settle(page);
        const wide = await view(page);
        check(
          wide != null && wide.zoom >= wide.min - 0.01,
          "after resizing back the zoom is below the fit zoom",
        );
        check(
          wide != null && inside(wide, wide.limit),
          "after resizing back the view left the frame",
        );

        const paper = await inks(page);
        await page.evaluate(() => {
          document.documentElement.classList.toggle("dark");
        });
        await page.waitForTimeout(700);
        check((await inks(page)) > 1, "map blank after a theme switch");
        await page.evaluate(() => {
          document.documentElement.classList.toggle("dark");
        });
        await page.waitForTimeout(400);
        check(paper > 0, "no paper color");

        await page.screenshot({ path: `${out}/${label}-end.png` });
      } catch (error) {
        failures.push(
          `crash: ${String(error).slice(0, 300)} ${/map-qa\.js:\d+/u.exec(String(error?.stack))?.[0] ?? ""}`,
        );
      }
      results.push({ label, failures });
      console.log(
        failures.length === 0 ? "pass" : "FAIL",
        label,
        failures.join(" | "),
      );
      await context.close();
    }
  }
}

await browser.close();
writeFileSync(`${out}/results.json`, JSON.stringify(results, null, 2));
const failed = results.filter((entry) => entry.failures.length > 0);
console.log(`${results.length - failed.length} of ${results.length} passed`);
process.exitCode = failed.length > 0 ? 1 : 0;
