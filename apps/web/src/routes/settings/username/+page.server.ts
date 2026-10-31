import { profile } from "@ea/db/schema";
import { fail, redirect } from "@sveltejs/kit";
import { eq } from "drizzle-orm";
import { m } from "#lib/paraglide/messages.js";
import { authDb } from "#lib/server/db/index.js";
import type { Actions, PageServerLoad } from "./$types";

const HANDLE = /^[a-z](?:[a-z0-9]|-(?!-)){1,28}[a-z0-9]$/u;

const RESERVED = new Set([
  "admin",
  "administrator",
  "api",
  "editor",
  "electionsarchive",
  "help",
  "mcp",
  "moderator",
  "official",
  "reviewer",
  "root",
  "staff",
  "support",
  "system",
]);

function isTaken(error: unknown) {
  for (let cause = error; cause instanceof Error; cause = cause.cause) {
    if ("code" in cause && cause.code === "23505") {
      return true;
    }
  }
  return false;
}

export const load: PageServerLoad = async ({ locals, url }) => {
  if (locals.user == null) {
    redirect(303, `/sign-in?next=${encodeURIComponent(url.pathname)}`);
  }
  const [found] = await authDb
    .select({ handle: profile.handle })
    .from(profile)
    .where(eq(profile.userId, locals.user.id));
  return { handle: found?.handle ?? null };
};

export const actions: Actions = {
  default: async ({ request, locals }) => {
    if (locals.user == null) {
      return fail(401, { message: m.username_signed_out() });
    }
    const form = await request.formData();
    const handle = String(form.get("handle") ?? "")
      .trim()
      .toLowerCase();
    if (!HANDLE.test(handle)) {
      return fail(400, { handle, message: m.username_invalid() });
    }
    if (RESERVED.has(handle)) {
      return fail(409, { handle, message: m.username_taken() });
    }
    try {
      await authDb
        .insert(profile)
        .values({ userId: locals.user.id, handle })
        .onConflictDoUpdate({ target: profile.userId, set: { handle } });
    } catch (error) {
      if (isTaken(error)) {
        return fail(409, { handle, message: m.username_taken() });
      }
      throw error;
    }
    return { saved: handle };
  },
};
