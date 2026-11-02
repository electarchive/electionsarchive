import { error, fail, isHttpError } from "@sveltejs/kit";
import * as v from "valibot";
import { decide, proposal, roles } from "#lib/server/contrib.js";
import {
  attempt,
  editAction,
  field,
  hideAction,
  Id,
} from "#lib/server/discussion-actions.js";
import {
  acceptSuggestion,
  AnchorSchema,
  Body,
  commentOnProposal,
  proposalThread,
  suggestableRows,
  SuggestionSchema,
  threadComments,
} from "#lib/server/discussion.js";
import { watching } from "#lib/server/feeds.js";
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
  if (found.submitted_at == null && locals.user?.id !== found.author_user_id) {
    error(404);
  }
  const isWatching =
    found.submitted_at != null &&
    (await watching(locals.user?.id, "proposal", found.id));
  const viewer = locals.user?.id;
  const comments = await threadComments(
    await proposalThread(found.id),
    viewer ?? null,
  );
  const isReviewer = allowed.admin || allowed.reviewer;
  const latest = found.revisions.at(0);
  const head = latest?.n;
  const isAuthor = viewer != null && viewer === found.author_user_id;
  const {
    prescreen,
    prereview,
    escalations,
    author_user_id: _author,
    ...rest
  } = found;
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
    isAuthor,
    suggestable:
      latest == null ||
      isAuthor ||
      !["open", "changes_requested"].includes(found.state)
        ? {}
        : suggestableRows(
            latest.payload.ops ?? [],
            latest.diff?.rows ?? [],
            latest.n,
          ),
    canAccept: isAuthor && ["open", "changes_requested"].includes(found.state),
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
    watch:
      found.submitted_at == null
        ? null
        : { id: found.id, watching: isWatching },
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
    const suggestionText = field(form, "suggestion");
    let suggestion = null;
    if (suggestionText != null) {
      let decoded: unknown = null;
      try {
        decoded = JSON.parse(suggestionText);
      } catch {}
      const parsed = v.safeParse(SuggestionSchema, decoded);
      if (!parsed.success) {
        return fail(400, { message: parsed.issues[0].message });
      }
      suggestion = parsed.output;
    }
    return (
      (await attempt(async () =>
        commentOnProposal({
          author: { userId, keyId: null, agentName: null, agentModel: null },
          number: parse(params.number),
          body: body.output,
          parentId: parent,
          anchor,
          suggestion,
        }),
      )) ?? { commented: true }
    );
  },
  edit_comment: editAction,
  hide_comment: hideAction,
  accept_suggestion: async ({ params, locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to accept suggestions." });
    }
    const userId = locals.user.id;
    const form = await request.formData();
    const id = field(form, "id");
    if (id == null || !v.is(Id, id)) {
      return fail(400, { message: "That suggestion isn't valid." });
    }
    return (
      (await attempt(async () =>
        acceptSuggestion({ userId, number: parse(params.number), id }),
      )) ?? { accepted: id }
    );
  },
  approve: decision("approve"),
  reject: decision("reject"),
  request_changes: decision("request_changes"),
};
