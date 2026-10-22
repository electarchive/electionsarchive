import { Buffer } from "node:buffer";
import { execFileSync, spawn } from "node:child_process";
import {
  createReadStream,
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { pages } from "./pages.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const site = path.join(here, "../public");
const types = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".woff2": "font/woff2",
  ".webp": "image/webp",
  ".png": "image/png",
  ".svg": "image/svg+xml",
};

const server = createServer((request, response) => {
  const pathname = path.normalize(
    decodeURIComponent(new URL(request.url, "http://x").pathname),
  );
  let file = path.join(site, pathname);
  if (existsSync(file) && statSync(file).isDirectory()) {
    file = path.join(file, "index.html");
  }
  if (!file.startsWith(site) || !existsSync(file)) {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, {
    "content-type": types[path.extname(file)] ?? "application/octet-stream",
  });
  createReadStream(file).pipe(response);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const { port } = server.address();
const scratch = mkdtempSync(path.join(tmpdir(), "shots-"));
const browser = spawn(
  "chromium-browser",
  [
    "--headless=new",
    "--hide-scrollbars",
    "--remote-debugging-port=0",
    `--user-data-dir=${scratch}`,
    "about:blank",
  ],
  { stdio: "ignore" },
);
const portFile = path.join(scratch, "DevToolsActivePort");
while (
  !existsSync(portFile) ||
  readFileSync(portFile, "utf8").split("\n").length < 2
) {
  await sleep(100);
}
const [debugPort, socketPath] = readFileSync(portFile, "utf8").split("\n", 2);
const socket = new WebSocket(`ws://127.0.0.1:${debugPort}${socketPath}`);
await new Promise((resolve) =>
  socket.addEventListener("open", resolve, { once: true }),
);
const sequence = { id: 0 };
const waiting = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  waiting.get(message.id)?.(message);
  waiting.delete(message.id);
});
const send = (method, params = {}, sessionId) =>
  new Promise((resolve) => {
    sequence.id += 1;
    waiting.set(sequence.id, resolve);
    socket.send(JSON.stringify({ id: sequence.id, method, params, sessionId }));
  });
const { result: target } = await send("Target.createTarget", {
  url: "about:blank",
});
const { result: session } = await send("Target.attachToTarget", {
  targetId: target.targetId,
  flatten: true,
});
const on = (method, params) => send(method, params, session.sessionId);
await on("Emulation.setDeviceMetricsOverride", {
  width: 1440,
  height: 1000,
  deviceScaleFactor: 1,
  mobile: false,
});

for (const showcase of pages) {
  for (const theme of ["light", "dark"]) {
    await on("Emulation.setEmulatedMedia", {
      features: [
        { name: "prefers-color-scheme", value: theme },
        { name: "prefers-reduced-motion", value: "reduce" },
      ],
    });
    await on("Page.navigate", {
      url: `http://127.0.0.1:${port}/showcase/${showcase.slug}/`,
    });
    await sleep(7000);
    const { result } = await on("Page.captureScreenshot", {
      format: "png",
      clip: { x: 0, y: 97, width: 1440, height: 900, scale: 1 },
    });
    const png = path.join(scratch, `${showcase.slug}-${theme}.png`);
    writeFileSync(png, Buffer.from(result.data, "base64"));
    execFileSync("magick", [
      png,
      "-resize",
      "1120x700",
      "-quality",
      "78",
      path.join(site, "showcase", showcase.slug, `${theme}.webp`),
    ]);
  }
  console.warn(showcase.slug);
}

socket.close();
const closed = new Promise((resolve) => browser.once("exit", resolve));
browser.kill();
await closed;
rmSync(scratch, { recursive: true, force: true });
server.close();
