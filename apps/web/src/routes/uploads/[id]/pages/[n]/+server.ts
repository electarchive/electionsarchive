import { error } from "@sveltejs/kit";
import * as v from "valibot";
import { page } from "#lib/server/uploads.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, locals }) => {
  const n = Number(params.n);
  if (
    !v.is(v.pipe(v.string(), v.uuid()), params.id) ||
    !Number.isSafeInteger(n) ||
    n < 1
  ) {
    error(404, "That upload has no such page.");
  }
  const { image, open } = await page(params.id, n, locals.user?.id);
  return new Response(new Uint8Array(image), {
    headers: {
      "content-type": "image/png",
      "content-disposition": "inline",
      "x-content-type-options": "nosniff",
      "cache-control": open
        ? "public, max-age=604800, immutable"
        : "private, max-age=3600",
    },
  });
};
