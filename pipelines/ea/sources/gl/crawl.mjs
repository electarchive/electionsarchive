// Reads every election on valg.gl, the Government of Greenland's election results site, with a headless browser.
// The site is a Blazor Server application, so a results table exists only after a person (or a browser) picks an
// election and a polling place in the page's dropdowns. For each election this writes one JSON file holding the
// overview, the seat list, the municipality and town filters, and the results table of every polling place.
//
//   node crawl.mjs <output folder> [election label ...]
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync, existsSync } from "node:fs";

const { chromium } = createRequire(import.meta.url)("playwright");
const SITE = "https://www.valg.gl";
const out = process.argv[2];
const only = process.argv.slice(3);
mkdirSync(out, { recursive: true });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function open(browser, path) {
  const page = await browser.newPage({ userAgent: "electionsarchive/0.1 (+https://electionsarchive.com)" });
  await page.goto(SITE + path, { waitUntil: "domcontentloaded", timeout: 90000 });
  page.on("crash", () => console.error("page crashed"));
  page.on("close", () => console.error("page closed"));
  await page.waitForSelector(".rz-dropdown", { timeout: 90000 });
  await sleep(2500);
  return page;
}

async function labels(page, index) {
  const id = await page.locator(".rz-dropdown").nth(index).getAttribute("id");
  return page.$$eval(`#popup-${id} li`, (items) => items.map((item) => item.getAttribute("aria-label")));
}

async function body(page) {
  return page.$eval(".rz-body", (element) => element.innerHTML.replace(/<svg[\s\S]*?<\/svg>/g, ""));
}

// The page redraws a moment after a choice, and on the way it shows the old table or the country's table. So wait
// until the content is none of the tables to avoid and has stopped changing, and read it once more to be sure.
async function settle(page, avoid) {
  let last = "";
  let steady = 0;
  for (let i = 0; i < 60; i++) {
    await sleep(400);
    const now = await body(page);
    if (now === last) steady++;
    else steady = 0;
    last = now;
    if (steady >= 4 && !avoid.includes(now)) {
      await sleep(800);
      if ((await body(page)) === now) return;
      steady = 0;
    }
    // A table equal to one to avoid that stays put for ten seconds really is that table.
    if (i >= 25 && steady >= 4) return;
  }
}

async function pick(page, index, label, avoid = []) {
  const dropdown = page.locator(".rz-dropdown").nth(index);
  const id = await dropdown.getAttribute("id");
  const before = await body(page);
  const item = page.locator(`#popup-${id} li[aria-label="${label}"]`);
  for (let attempt = 0; attempt < 3 && !(await item.isVisible()); attempt++) {
    await dropdown.click();
    await sleep(300);
  }
  await item.click();
  await settle(page, [before, ...avoid]);
}

async function go(page, path) {
  await page.evaluate((target) => document.querySelector(`a[href$="${target}"]`).click(), path);
  await sleep(3000);
  await page.waitForSelector(".rz-dropdown");
}

async function crawl(browser, election) {
  const file = `${out}/${election.replace(/ /g, "-")}.json`;
  if (existsSync(file)) return;
  const result = { election, retrieved_at: new Date().toISOString(), stations: {}, totals: {}, kommuner: {} };
  let page = await open(browser, "/");
  await pick(page, 0, election);
  result.overview = await body(page);
  await go(page, "/mandater-og-stedfortraedere");
  result.mandates = await body(page);
  await go(page, "/resultater");
  result.totals["Alle"] = await body(page);
  const kommuner = (await labels(page, 1)).filter((label) => !label.startsWith("Alle"));
  const all = (await labels(page, 3)).filter((label) => !label.startsWith("Alle"));
  for (const kommune of kommuner) {
    for (let attempt = 0; attempt < 4; attempt++) {
      await pick(page, 1, kommune, [result.totals["Alle"]]);
      // A table that holds no party yet has not finished drawing.
      if ((await body(page)).includes("party-logo")) break;
      await pick(page, 1, "Alle Kommuner");
    }
    result.kommuner[kommune] = (await labels(page, 3)).filter((label) => !label.startsWith("Alle"));
    result.totals[kommune] = await body(page);
  }
  console.log(election, "kommuner", kommuner.length, "stations", all.length);
  await page.close();
  page = await open(browser, "/resultater");
  await pick(page, 0, election);
  const everything = await body(page);
  for (const station of all) {
    for (let attempt = 0; ; attempt++) {
      try {
        await pick(page, 3, station, [everything]);
        result.stations[station] = await body(page);
        break;
      } catch (error) {
        if (attempt >= 3) throw error;
        await sleep(2000);
      }
    }
  }
  console.log(election, "done", Object.keys(result.stations).length);
  await page.close();
  writeFileSync(file, JSON.stringify(result));
}

const browser = await chromium.launch();
const first = await open(browser, "/");
let elections = await labels(first, 0);
await first.close();
if (only.length) elections = elections.filter((label) => only.includes(label));
writeFileSync(`${out}/elections.json`, JSON.stringify(elections));
const queue = [...elections];
const workers = Array.from({ length: 2 }, async () => {
  while (queue.length) {
    const election = queue.shift();
    for (let attempt = 0; ; attempt++) {
      try {
        await crawl(browser, election);
        break;
      } catch (error) {
        console.error("retry", election, String(error.stack).slice(0, 600));
        if (attempt >= 2) break;
      }
    }
  }
});
await Promise.all(workers);
await browser.close();
