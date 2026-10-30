import { error, fail, isHttpError } from "@sveltejs/kit";
import * as v from "valibot";
import { decide, proposal, roles } from "#lib/server/contrib.js";
import {
  AnchorSchema,
  Body,
  commentOnProposal,
  editComment,
  hideComment,
  proposalThread,
  threadComments,
} from "#lib/server/discussion.js";
import type { Action, Actions, PageServerLoad } from "./$types";

const parse = (value: string) => {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) {
    error(404);
  }
  return number;
};

export const load: PageServerLoad = async ({ params, locals, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const [found, allowed] = await Promise.all([
    proposal(parse(params.number)),
    roles(locals.user?.id),
  ]);
  const viewer = locals.user?.id;
  const comments = await threadComments(
    await proposalThread(found.id),
    viewer ?? null,
  );
  const isReviewer = allowed.admin || allowed.reviewer;
  const head = found.revisions.at(0)?.n;
  const { prescreen, prereview, escalations, ...rest } = found;
  const isCurrent = prescreen != null && prescreen.revision === head;
  return {
    proposal: {
      ...rest,
      prescreen: isCurrent
        ? {
            verdict: prescreen.verdict,
            reason: isReviewer ? prescreen.reason : null,
          }
        : null,
      prereview,
      escalations: isReviewer ? escalations : [],
      revisions: found.revisions.map((revision) => ({
        ...revision,
        sources: (revision.sources ?? []).map(({ scan, ...item }) => ({
          ...item,
          scan:
            scan != null &&
            (scan.shared ||
              scan.owner === viewer ||
              allowed.admin ||
              allowed.reviewer)
              ? {
                  upload: scan.upload,
                  page: scan.page,
                  width: scan.width,
                  height: scan.height,
                  region: scan.region,
                }
              : null,
        })),
      })),
    },
    comments,
    commenter:
      locals.user == null
        ? ("signed_out" as const)
        : locals.user.emailVerified
          ? ("ready" as const)
          : ("unverified" as const),
    canModerate: isReviewer,
    canDecide: found.state === "open" && isReviewer,
    canReject: allowed.admin || found.tier === 1,
    canApprove: allowed.admin || (found.tier === 1 && escalations.length === 0),
  };
};

const decision =
  (verb: "approve" | "reject" | "request_changes"): Action =>
  async ({ params, locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to decide proposals." });
    }
    const form = await request.formData();
    const reason = String(form.get("reason") ?? "")
      .normalize("NFC")
      .trim()
      .slice(0, 2000);
    try {
      await decide({
        userId: locals.user.id,
        number: parse(params.number),
        verb,
        reason,
      });
    } catch (error_) {
      if (isHttpError(error_)) {
        return fail(error_.status, { message: error_.body.message });
      }
      throw error_;
    }
    return { decided: verb };
  };

const attempt = async (work: () => Promise<unknown>) => {
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

const field = (form: FormData, name: string) => {
  const value = form.get(name);
  return typeof value === "string" && value !== "" ? value : null;
};

const Id = v.pipe(v.string(), v.uuid());

export const actions: Actions = {
  comment: async ({ params, locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to comment." });
    }
    const userId = locals.user.id;
    const form = await request.formData();
    const body = v.safeParse(Body, form.get("body") ?? "");
    if (!body.success) {
      return fail(400, { message: body.issues[0].message });
    }
    const anchorText = field(form, "anchor");
    let anchor = null;
    if (anchorText != null) {
      let decoded: unknown = null;
      try {
        decoded = JSON.parse(anchorText);
      } catch {}
      const parsed = v.safeParse(AnchorSchema, decoded);
      if (!parsed.success) {
        return fail(400, { message: "That anchor isn't valid." });
      }
      anchor = parsed.output;
    }
    const parent = field(form, "parent");
    if (parent != null && !v.is(Id, parent)) {
      return fail(400, { message: "That reply target isn't valid." });
    }
    return (
      (await attempt(async () =>
        commentOnProposal({
          author: { userId, keyId: null, agentName: null, agentModel: null },
          number: parse(params.number),
          body: body.output,
          parentId: parent,
          anchor,
        }),
      )) ?? { commented: true }
    );
  },
  edit_comment: async ({ locals, request }) => {
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
  },
  hide_comment: async ({ locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to moderate." });
    }
    const userId = locals.user.id;
    const form = await request.formData();
    const id = field(form, "id");
    if (id == null || !v.is(Id, id)) {
      return fail(400, { message: "That comment isn't valid." });
    }
    const reason =
      form.get("restore") == null
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
  },
  approve: decision("approve"),
  reject: decision("reject"),
  request_changes: decision("request_changes"),
};
