import { error } from "@sveltejs/kit";
import { readFile } from "node:fs/promises";
import path from "node:path";
import type { RequestHandler } from "./$types";

const ROOT = path.resolve("../../data/photos");

export const GET: RequestHandler = async ({ params }) => {
  if (!/^[0-9a-f]{64}\.jpg$/u.test(params.file)) {
    error(404);
  }
  let body;
  try {
    body = await readFile(path.join(ROOT, params.file));
  } catch {
    error(404);
  }
  return new Response(body, {
    headers: {
      "cache-control": "public, max-age=31536000, immutable",
      "content-type": "image/jpeg",
      "content-length": String(body.length),
    },
  });
};
