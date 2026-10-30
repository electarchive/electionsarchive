import { ORPCError } from "@orpc/server";
import { isHttpError } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { auth } from "#lib/server/auth.js";
import { db } from "#lib/server/db/index.js";

const codes: Record<number, string> = {
  400: "BAD_REQUEST",
  401: "UNAUTHORIZED",
  403: "FORBIDDEN",
  404: "NOT_FOUND",
  409: "CONFLICT",
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

const Metadata = v.looseObject({
  agent_name: v.optional(v.string()),
  agent_model: v.optional(v.string()),
});

export async function keyOwner(headers: Headers) {
  const key = headers.get("x-api-key");
  if (key == null || key === "") {
    return null;
  }
  const verified = await auth.api.verifyApiKey({ body: { key } });
  if (!verified.valid || verified.key == null) {
    throw new ORPCError("UNAUTHORIZED");
  }
  const userId = verified.key.referenceId;
  const { rows } = await db.execute<{ banned: boolean }>(sql`
    select coalesce(banned, false) and (ban_expires is null or ban_expires > now()) as banned
    from users where id = ${userId}
  `);
  if (rows[0] == null || rows[0].banned) {
    throw new ORPCError("FORBIDDEN");
  }
  const metadata = v.safeParse(Metadata, verified.key.metadata);
  return {
    userId,
    keyId: verified.key.id,
    agentName: metadata.success ? (metadata.output.agent_name ?? null) : null,
    agentModel: metadata.success ? (metadata.output.agent_model ?? null) : null,
  };
}

export const Number_ = v.pipe(v.string(), v.regex(/^[1-9]\d{0,8}$/));

export const Text = (max: number) =>
  v.pipe(
    v.string(),
    v.normalize("NFC"),
    v.trim(),
    v.minLength(1),
    v.maxLength(max),
  );
