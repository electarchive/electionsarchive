import { error, fail, isHttpError } from "@sveltejs/kit";
import { decide, proposal, roles } from "#lib/server/contrib.js";
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

export const actions: Actions = {
  approve: decision("approve"),
  reject: decision("reject"),
  request_changes: decision("request_changes"),
};
