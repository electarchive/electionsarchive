import { error } from "@sveltejs/kit";
import { readFile } from "node:fs/promises";
import path from "node:path";
import type { RequestHandler } from "./$types";

const ROOT = path.resolve("../../data/pages");

export const GET: RequestHandler = async ({ params }) => {
  if (
    !/^[a-z]{2}\.[a-z0-9_]+$/u.test(params.scheme) ||
    !/^[\w-]+\.png$/u.test(params.file)
  ) {
    error(404);
  }
  let image;
  try {
    image = await readFile(path.join(ROOT, params.scheme, params.file));
  } catch {
    error(404);
  }
  return new Response(image, {
    headers: {
      "cache-control": "public, max-age=604800, immutable",
      "content-type": "image/png",
    },
  });
};
