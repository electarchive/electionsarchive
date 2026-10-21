import { error, redirect } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { db } from "#lib/server/db/index.js";
import type { RequestHandler } from "./$types";

const ROOT = path.resolve("../../data/photos");

export const GET: RequestHandler = async ({ params }) => {
  const format = /^[0-9a-f]{64}\.(webp|jpg)$/u.exec(params.file)?.[1];
  if (format == null) {
    error(404);
  }
  let body;
  try {
    body = await readFile(path.join(ROOT, params.file));
  } catch {
    if (format === "jpg") {
      const found = await db.execute<{ path: string }>(sql`
        select current.path from person_image current
        where current.id in (select id from person_image_history where path = ${`photos/${params.file}`})
        limit 1
      `);
      if (found.rows[0] != null) {
        redirect(301, `/${found.rows[0].path}`);
      }
    }
    error(404);
  }
  return new Response(body, {
    headers: {
      "cache-control": "public, max-age=31536000, immutable",
      "content-type": format === "webp" ? "image/webp" : "image/jpeg",
      "content-length": String(body.length),
    },
  });
};
