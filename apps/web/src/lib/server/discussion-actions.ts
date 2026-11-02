import type { Action } from "@sveltejs/kit";
import { fail, isHttpError } from "@sveltejs/kit";
import * as v from "valibot";
import { Body, editComment, hideComment } from "#lib/server/discussion.js";

export const Id = v.pipe(v.string(), v.uuid());

export const commenterOf = (user: App.Locals["user"]) =>
  user == null
    ? ("signed_out" as const)
    : user.emailVerified
      ? ("ready" as const)
      : ("unverified" as const);

export const field = (form: FormData, name: string) => {
  const value = form.get(name);
  return typeof value === "string" && value !== "" ? value : null;
};

export const attempt = async (work: () => Promise<unknown>) => {
  try {
    await work();
  } catch (error_) {
    if (isHttpError(error_)) {
      return fail(error_.status, { message: error_.body.message });
    }
    throw error_;
  }
  return null;
};

export const editAction: Action = async ({ locals, request }) => {
  if (locals.user == null) {
    return fail(401, { message: "Sign in to edit." });
  }
  const userId = locals.user.id;
  const form = await request.formData();
  const id = field(form, "id");
  const body = v.safeParse(Body, form.get("body") ?? "");
  if (id == null || !v.is(Id, id)) {
    return fail(400, { message: "That comment isn't valid." });
  }
  if (!body.success) {
    return fail(400, { message: body.issues[0].message });
  }
  return (
    (await attempt(async () =>
      editComment({ userId, id, body: body.output }),
    )) ?? { edited: id }
  );
};

export const hideAction: Action = async ({ locals, request }) => {
  if (locals.user == null) {
    return fail(401, { message: "Sign in to moderate." });
  }
  const userId = locals.user.id;
  const form = await request.formData();
  const id = field(form, "id");
  if (id == null || !v.is(Id, id)) {
    return fail(400, { message: "That comment isn't valid." });
  }
  const reason = !form.has("restore")
    ? String(form.get("reason") ?? "")
        .normalize("NFC")
        .trim()
        .slice(0, 500)
    : null;
  return (
    (await attempt(async () => hideComment({ userId, id, reason }))) ?? {
      hidden: id,
    }
  );
};
