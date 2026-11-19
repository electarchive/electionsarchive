import { error } from "@sveltejs/kit";
import { readFile } from "node:fs/promises";
import path from "node:path";
import type { RequestHandler } from "./$types";

const ROOT = path.resolve("../../data/pages");

const TYPES = { jpg: "image/jpeg", png: "image/png" } as const;

export const GET: RequestHandler = async ({ params }) => {
  const extension = /^(?:[\w-]+\/)*[\w-]+\.(png|jpg)$/u.exec(params.file)?.[1];
  if (
    !/^[a-z]{2,3}(-[a-z0-9]+)?\.[a-z0-9_]+$/u.test(params.scheme) ||
    (extension !== "png" && extension !== "jpg")
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
      "content-type": TYPES[extension],
    },
  });
};
