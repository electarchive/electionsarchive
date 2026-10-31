import { Buffer } from "node:buffer";
import { spawn } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const site = path.join(here, "public");
const fps = 30;
const scratch = mkdtempSync(path.join(tmpdir(), "og-"));
const browser = spawn(
  "chromium-browser",
  [
    "--headless=new",
    "--hide-scrollbars",
    "--allow-file-access-from-files",
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
const evaluate = async (expression) => {
  const { result } = await on("Runtime.evaluate", {
    expression,
    returnByValue: true,
  });
  return result.result.value;
};

async function open(scale) {
  await on("Emulation.setDeviceMetricsOverride", {
    width: 1200,
    height: 630,
    deviceScaleFactor: scale,
    mobile: false,
  });
  await on("Page.navigate", {
    url: pathToFileURL(path.join(here, "og.html")).href,
  });
  while ((await evaluate("document.body?.dataset.ready")) !== "true") {
    await sleep(100);
  }
}

async function shoot(t) {
  await evaluate(`frame(${t})`);
  const { result } = await on("Page.captureScreenshot", { format: "png" });
  return Buffer.from(result.data, "base64");
}

await open(2);
const end = await evaluate("END");
writeFileSync(path.join(site, "og.png"), await shoot(end));

await open(1.6);
const encoder = spawn(
  process.env.FFMPEG ?? "ffmpeg",
  [
    "-y",
    "-loglevel",
    "error",
    "-f",
    "image2pipe",
    "-framerate",
    `${fps}`,
    "-c:v",
    "png",
    "-i",
    "-",
    "-c:v",
    "libx264",
    "-preset",
    "slow",
    "-crf",
    "20",
    "-pix_fmt",
    "yuv420p",
    "-movflags",
    "+faststart",
    "-an",
    path.join(site, "og.mp4"),
  ],
  { stdio: ["pipe", "inherit", "inherit"] },
);
const encoded = new Promise((resolve) => encoder.once("exit", resolve));
for (let i = 0; i <= Math.round(end * fps); i++) {
  const png = await shoot(i / fps);
  if (!encoder.stdin.write(png)) {
    await new Promise((resolve) => encoder.stdin.once("drain", resolve));
  }
}
encoder.stdin.end();
await encoded;

socket.close();
const closed = new Promise((resolve) => browser.once("exit", resolve));
browser.kill();
await closed;
rmSync(scratch, { recursive: true, force: true });
