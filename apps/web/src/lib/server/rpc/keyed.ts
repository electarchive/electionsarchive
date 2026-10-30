import { os as base, ORPCError } from "@orpc/server";
import { isHttpError } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { PROJECT } from "#lib/constants.js";
import { auth } from "#lib/server/auth.js";
import { db } from "#lib/server/db/index.js";
import { createRateLimiter } from "#lib/server/ratelimit.js";

const codes: Record<number, string> = {
  400: "BAD_REQUEST",
  401: "UNAUTHORIZED",
  403: "FORBIDDEN",
  404: "NOT_FOUND",
  409: "CONFLICT",
  413: "PAYLOAD_TOO_LARGE",
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

export const WRITES = { minute: 60, day: 2000 };

const limit = createRateLimiter({ prefix: `${PROJECT}:keys` });

const os = base.$context<{ headers: Headers }>();

export const reader = os.use(async ({ context, next }) =>
  next({ context: { owner: await keyOwner(context.headers) } }),
);

const required = async (headers: Headers) => {
  const owner = await keyOwner(headers);
  if (owner == null) {
    throw new ORPCError("UNAUTHORIZED", {
      message:
        "Send your API key in the x-api-key header or as a Bearer token.",
    });
  }
  return owner;
};

export const keyed = os.use(async ({ context, next }) =>
  next({ context: { owner: await required(context.headers) } }),
);

export const writer = os.use(async ({ context, next }) => {
  const owner = await required(context.headers);
  const { isAllowed, retryAfter } = await limit(
    {
      name: "writes",
      rules: [
        { limit: WRITES.minute, window: 60 },
        { limit: WRITES.day, window: 86_400 },
      ],
    },
    owner.keyId,
  );
  if (!isAllowed) {
    throw new ORPCError("TOO_MANY_REQUESTS", {
      message: `A key may make 60 writes a minute and 2,000 a day. Try again in ${retryAfter} s.`,
    });
  }
  return next({ context: { owner } });
});
