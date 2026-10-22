import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pages } from "./pages.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "../../..");
const web = path.join(root, "apps/web/node_modules");
const out = path.join(root, "apps/landing/public/showcase");

if (!existsSync(path.join(here, "node_modules"))) {
  symlinkSync(web, path.join(here, "node_modules"));
}
execFileSync("pnpm", ["--filter", "@ea/web", "compile-paraglide"], {
  cwd: root,
  stdio: "inherit",
});

const { build } = await import(path.join(web, "vite/dist/node/index.js"));
await build({ configFile: path.join(here, "vite.config.mjs") });

const manifest = JSON.parse(
  readFileSync(path.join(out, "_app/.vite/manifest.json"), "utf8"),
);
rmSync(path.join(out, "_app/.vite"), { recursive: true, force: true });
const entry = manifest["src/main.ts"];
const styles = entry.css ?? [];
const imports = (entry.imports ?? []).map((name) => manifest[name].file);

const escape = (text) =>
  text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll('"', "&quot;");

for (const [index, showcase] of pages.entries()) {
  const folder = path.join(out, showcase.slug);
  mkdirSync(folder, { recursive: true });
  writeFileSync(
    path.join(folder, "index.html"),
    `<!doctype html>
<html lang="en" data-showcase="${showcase.slug}">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <title>${escape(showcase.title)} | Elections Archive preview</title>
    <meta name="description" content="${escape(showcase.caption)}" />
    <meta name="robots" content="noindex" />
    <meta name="color-scheme" content="light dark" />
    <meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff" />
    <meta name="theme-color" media="(prefers-color-scheme: dark)" content="#111315" />
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <link rel="icon" href="/favicon.png" type="image/png" />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
    <style>
      html { background: #ffffff; color: #15171a; }
      @media (prefers-color-scheme: dark) { html { background: #111315; color: #e8eaed; } }
      .boot { display: grid; place-items: center; min-block-size: 70dvh; font: 600 0.875rem/1.4 system-ui, sans-serif; color: #5b616a; }
      @media (prefers-reduced-motion: no-preference) { .boot { animation: boot 1.2s ease-in-out infinite alternate; } }
      @keyframes boot { from { opacity: 0.35; } to { opacity: 1; } }
    </style>
${styles.map((file) => `    <link rel="stylesheet" href="/showcase/_app/${file}" />`).join("\n")}
${imports.map((file) => `    <link rel="modulepreload" href="/showcase/_app/${file}" />`).join("\n")}
    <link rel="preload" href="/showcase/${showcase.slug}/views/0.json" as="fetch" crossorigin="anonymous" />
    <script type="module" src="/showcase/_app/${entry.file}"></script>
  </head>
  <body>
    <div id="app"><p class="boot">${String(index + 1).padStart(2, "0")}/${pages.length} · ${escape(showcase.title)}</p></div>
  </body>
</html>
`,
  );
}
const landing = path.join(root, "apps/landing/public/index.html");
const html = readFileSync(landing, "utf8");
const start = html.indexOf("<!-- showcase -->");
const end = html.indexOf("<!-- /showcase -->");
const tiles = pages
  .map(
    (showcase) => `          <li class="tile">
            <a href="/showcase/${showcase.slug}/">
              <picture>
                <source srcset="/showcase/${showcase.slug}/dark.webp" media="(prefers-color-scheme: dark)" />
                <img src="/showcase/${showcase.slug}/light.webp" width="1120" height="700" loading="lazy" decoding="async" alt="" />
              </picture>
              <span class="title" style="view-transition-name: showcase-${showcase.slug}">${escape(showcase.title)}</span>
              <span class="caption">${escape(showcase.caption)}</span>
            </a>
          </li>`,
  )
  .join("\n");
writeFileSync(
  landing,
  `${html.slice(0, start + 17)}\n        <ul class="tiles">\n${tiles}\n        </ul>\n        ${html.slice(end)}`,
);
console.warn(`${pages.length} pages written`);
