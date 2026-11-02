import { READ_ONLY } from "$app/env/public";
import { error, fail, isHttpError, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { revert, roles } from "#lib/server/contrib.js";
import { change } from "#lib/server/history.js";
import { PublicId } from "#lib/server/talk.js";
import type { Actions, PageServerLoad } from "./$types";

const Changeset = v.pipe(v.string(), v.uuid());

const parsed = (params: { id: string; changeset: string }) => {
  const id = v.safeParse(PublicId, params.id);
  if (!id.success || !v.is(Changeset, params.changeset)) {
    error(404);
  }
  return { id: id.output, changeset: params.changeset };
};

export const load: PageServerLoad = async ({ params, locals, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const { id, changeset } = parsed(params);
  const [found, allowed] = await Promise.all([
    change(id, changeset),
    roles(locals.user?.id),
  ]);
  if (found.entity.public_id !== params.id) {
    redirect(301, `/history/${found.entity.public_id}/${changeset}`);
  }
  return {
    ...found,
    revertible:
      !READ_ONLY &&
      found.entry.number != null &&
      found.entry.state === "published" &&
      (allowed.admin || allowed.reviewer),
  };
};

export const actions: Actions = {
  revert: async ({ params, locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to revert a change." });
    }
    const { id, changeset } = parsed(params);
    const reason = String(
      (await request.formData()).get("reason") ?? "",
    ).trim();
    if (reason.length < 3) {
      return fail(400, { message: "Give a reason for the revert." });
    }
    const { entry } = await change(id, changeset);
    if (entry.number == null) {
      return fail(409, {
        message: "Only a contributor's proposal can be reverted here.",
      });
    }
    try {
      await revert({ userId: locals.user.id, number: entry.number, reason });
    } catch (error_) {
      if (isHttpError(error_)) {
        return fail(error_.status, { message: error_.body.message });
      }
      throw error_;
    }
    return { queued: true };
  },
};
