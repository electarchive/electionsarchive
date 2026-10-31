import { fail, redirect } from "@sveltejs/kit";
import { isAPIError } from "better-auth/api";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { m } from "#lib/paraglide/messages.js";
import { auth } from "#lib/server/auth.js";
import { authDb } from "#lib/server/db/index.js";
import type { Actions, PageServerLoad } from "./$types";

const KEY_LIMIT = 10;

const lifetimes = {
  "30": 30 * 86_400,
  "90": 90 * 86_400,
  "365": 365 * 86_400,
  never: null,
} as const;

const Line = (max: number) =>
  v.pipe(
    v.string(),
    v.normalize("NFC"),
    v.trim(),
    v.minLength(1),
    v.maxLength(max),
  );

const Created = v.object({
  name: Line(60),
  agentName: Line(100),
  agentModel: Line(100),
  lifetime: v.picklist(["30", "90", "365", "never"]),
});

async function keys(userId: string) {
  const { rows } = await authDb.execute<{
    id: string;
    name: string | null;
    start: string | null;
    agent_name: string | null;
    agent_model: string | null;
    created_at: string;
    last_request: string | null;
    expires_at: string | null;
    expired: boolean;
  }>(sql`
    select id, name, start, metadata::jsonb ->> 'agent_name' as agent_name,
           metadata::jsonb ->> 'agent_model' as agent_model, created_at::text,
           last_request::text, expires_at::text, coalesce(expires_at < now(), false) as expired
    from apikeys
    where reference_id = ${userId}
    order by created_at desc
  `);
  return rows;
}

export const load: PageServerLoad = async ({ locals, url }) => {
  if (locals.user == null) {
    redirect(303, `/sign-in?next=${encodeURIComponent(url.pathname)}`);
  }
  return { keys: await keys(locals.user.id), limit: KEY_LIMIT };
};

export const actions: Actions = {
  create: async ({ request, locals }) => {
    if (locals.user == null) {
      return fail(401, { message: m.keys_signed_out() });
    }
    const form = await request.formData();
    const parsed = v.safeParse(Created, {
      name: form.get("name"),
      agentName: form.get("agentName"),
      agentModel: form.get("agentModel"),
      lifetime: form.get("lifetime"),
    });
    if (!parsed.success) {
      return fail(400, { message: m.keys_invalid() });
    }
    const existing = await keys(locals.user.id);
    if (existing.length >= KEY_LIMIT) {
      return fail(400, { message: m.keys_limit({ count: KEY_LIMIT }) });
    }
    const { name, agentName, agentModel, lifetime } = parsed.output;
    try {
      const created = await auth.api.createApiKey({
        body: {
          name,
          expiresIn: lifetimes[lifetime],
          metadata: { agent_name: agentName, agent_model: agentModel },
        },
        headers: request.headers,
      });
      return { created: { key: created.key, name } };
    } catch (error) {
      if (isAPIError(error)) {
        return fail(400, { message: error.message });
      }
      throw error;
    }
  },
  revoke: async ({ request, locals }) => {
    if (locals.user == null) {
      return fail(401, { message: m.keys_signed_out() });
    }
    const form = await request.formData();
    const id = String(form.get("id") ?? "");
    try {
      await auth.api.deleteApiKey({
        body: { keyId: id },
        headers: request.headers,
      });
    } catch (error) {
      if (isAPIError(error)) {
        return fail(400, { message: error.message });
      }
      throw error;
    }
    return { revoked: true };
  },
};
