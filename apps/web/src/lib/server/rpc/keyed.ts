import { os as base, ORPCError } from "@orpc/server";
import { isHttpError } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { auth } from "#lib/server/auth.js";
import { authDb } from "#lib/server/db/index.js";

const codes: Record<number, string> = {
  400: "BAD_REQUEST",
  401: "UNAUTHORIZED",
  403: "FORBIDDEN",
  404: "NOT_FOUND",
  429: "TOO_MANY_REQUESTS",
};

export async function run<T>(work: () => Promise<T>) {
  try {
    return await work();
  } catch (error_) {
    if (isHttpError(error_)) {
      throw new ORPCError(codes[error_.status] ?? "BAD_REQUEST", {
        message: error_.body.message,
      });
    }
    throw error_;
  }
}

export async function keyOwner(headers: Headers) {
  const key =
    headers.get("x-api-key") ??
    /^Bearer\s+(ea_\S+)$/iu.exec(headers.get("authorization") ?? "")?.[1];
  if (key == null || key === "") {
    return null;
  }
  const verified = await auth.api.verifyApiKey({ body: { key } });
  if (!verified.valid || verified.key == null) {
    throw new ORPCError("UNAUTHORIZED");
  }
  const userId = verified.key.referenceId;
  const { rows } = await authDb.execute<{ banned: boolean }>(sql`
    select coalesce(banned, false) and (ban_expires is null or ban_expires > now()) as banned
    from users where id = ${userId}
  `);
  if (rows[0] == null || rows[0].banned) {
    throw new ORPCError("FORBIDDEN");
  }
  return { userId, keyId: verified.key.id };
}

const os = base.$context<{ headers: Headers }>();

export const reader = os.use(async ({ context, next }) =>
  next({ context: { owner: await keyOwner(context.headers) } }),
);
