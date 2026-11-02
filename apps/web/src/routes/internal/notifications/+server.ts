import { NOTIFY_TOKEN } from "$app/env/private";
import { error, json } from "@sveltejs/kit";
import { createHash, timingSafeEqual } from "node:crypto";
import { deliver } from "#lib/server/notifications.js";
import type { RequestHandler } from "./$types";

const digest = (text: string) => createHash("sha256").update(text).digest();

export const POST: RequestHandler = async ({ request }) => {
  const given = request.headers.get("authorization")?.replace(/^Bearer /u, "");
  if (
    NOTIFY_TOKEN == null ||
    given == null ||
    !timingSafeEqual(digest(given), digest(NOTIFY_TOKEN))
  ) {
    error(401, "Unauthorized");
  }
  return json(await deliver());
};
