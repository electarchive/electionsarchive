import { error, fail, isHttpError } from "@sveltejs/kit";
import * as v from "valibot";
import {
  account,
  ban,
  BanForm,
  liftLimit,
  LimitForm,
  moderator,
  RevertForm,
  revertSince,
  setLimit,
  unban,
} from "#lib/server/accounts.js";
import { roles } from "#lib/server/contrib.js";
import { field, Id } from "#lib/server/discussion-actions.js";
import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ locals, params, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  if (!v.is(Id, params.id)) {
    error(404, "No account has that ID.");
  }
  const allowed = await roles(locals.user?.id);
  if (!(allowed.admin || allowed.reviewer)) {
    return { allowed: false as const, admin: false };
  }
  return {
    allowed: true as const,
    admin: allowed.admin,
    ...(await account(params.id)),
  };
};

const fields = (form: FormData, names: Array<string>) =>
  Object.fromEntries(names.map((name) => [name, field(form, name) ?? ""]));

const run = async (action: string, work: () => Promise<number | void>) => {
  try {
    const result = await work();
    return { action, done: true as const, queued: result ?? null };
  } catch (error_) {
    if (isHttpError(error_)) {
      return fail(error_.status, { action, message: error_.body.message });
    }
    throw error_;
  }
};

export const actions: Actions = {
  limit: async ({ locals, params, request }) => {
    const actor = await moderator(locals.user?.id);
    const parsed = v.safeParse(
      LimitForm,
      fields(await request.formData(), ["kind", "proposals", "reason", "days"]),
    );
    if (!parsed.success) {
      return fail(400, { action: "limit", message: parsed.issues[0].message });
    }
    return run("limit", async () => setLimit(actor, params.id, parsed.output));
  },
  lift: async ({ locals, params, request }) => {
    const actor = await moderator(locals.user?.id);
    const id = field(await request.formData(), "limit");
    if (id == null || !v.is(Id, id)) {
      return fail(400, { action: "lift", message: "That limit isn't valid." });
    }
    return run("lift", async () => liftLimit(actor, params.id, id));
  },
  ban: async ({ locals, params, request }) => {
    const actor = await moderator(locals.user?.id);
    const parsed = v.safeParse(
      BanForm,
      fields(await request.formData(), ["reason", "days"]),
    );
    if (!parsed.success) {
      return fail(400, { action: "ban", message: parsed.issues[0].message });
    }
    return run("ban", async () => ban(actor, params.id, parsed.output));
  },
  unban: async ({ locals, params }) => {
    const actor = await moderator(locals.user?.id);
    return run("unban", async () => unban(actor, params.id));
  },
  revert: async ({ locals, params, request }) => {
    const actor = await moderator(locals.user?.id);
    const parsed = v.safeParse(
      RevertForm,
      fields(await request.formData(), ["since", "reason"]),
    );
    if (!parsed.success) {
      return fail(400, { action: "revert", message: parsed.issues[0].message });
    }
    return run("revert", async () =>
      revertSince(actor, params.id, parsed.output),
    );
  },
};
