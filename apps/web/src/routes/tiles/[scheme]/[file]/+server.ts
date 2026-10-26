import { error } from "@sveltejs/kit";
import { Buffer } from "node:buffer";
import { createReadStream } from "node:fs";
import { open, stat } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import type { RequestHandler } from "./$types";

const ROOT = path.resolve("../../data/tiles");

export const GET: RequestHandler = async ({ params, request }) => {
  if (
    !/^[a-z]{2}\.[a-z_]+$/u.test(params.scheme) ||
    !/^[\w-]+(?:\.ids)?\.pmtiles$/u.test(params.file)
  ) {
    error(404);
  }
  const file = path.join(ROOT, params.scheme, params.file);
  let info;
  try {
    info = await stat(file);
  } catch {
    error(404);
  }
  const headers = {
    "accept-ranges": "bytes",
    "cache-control": "public, max-age=86400",
    "content-type": "application/vnd.pmtiles",
  };
  const range = /^bytes=(\d+)-(\d*)$/u.exec(request.headers.get("range") ?? "");
  if (range == null) {
    return new Response(
      Readable.toWeb(createReadStream(file)) as ReadableStream,
      { headers: { ...headers, "content-length": String(info.size) } },
    );
  }
  const start = Number(range[1]);
  const end = Math.min(
    range[2] === "" ? info.size - 1 : Number(range[2]),
    info.size - 1,
  );
  if (start > end) {
    return new Response(null, {
      status: 416,
      headers: { "content-range": `bytes */${info.size}` },
    });
  }
  const handle = await open(file);
  const buffer = Buffer.alloc(end - start + 1);
  await handle.read(buffer, 0, buffer.length, start);
  await handle.close();
  return new Response(buffer, {
    status: 206,
    headers: {
      ...headers,
      "content-length": String(buffer.length),
      "content-range": `bytes ${start}-${end}/${info.size}`,
    },
  });
};
