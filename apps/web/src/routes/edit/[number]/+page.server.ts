import { error, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { view } from "#lib/server/rpc/proposals.js";
import {
  latest,
  records,
  refsOf,
  sheet,
  upload,
  Workspace,
} from "#lib/server/workspace.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({
  params,
  locals,
  url,
  setHeaders,
}) => {
  if (locals.user == null) {
    redirect(303, `/sign-in?next=${encodeURIComponent(url.pathname)}`);
  }
  const number = Number(params.number);
  if (!Number.isSafeInteger(number) || number < 1) {
    error(404);
  }
  setHeaders({ "cache-control": "no-store" });
  const found = await view(number, {
    userId: locals.user.id,
    keyId: null,
    agentName: null,
    agentModel: null,
  });
  const workspace = v.safeParse(Workspace, found.draft?.workspace);
  if (
    found.state !== "draft" ||
    found.revisions.length > 0 ||
    !workspace.success
  ) {
    redirect(303, `/proposals/${number}`);
  }
  const output = workspace.output;
  const looked = await records(refsOf(output));
  const contests = new Set(
    output.template === "results"
      ? Object.values(looked).flatMap((item) =>
          item.state === "found" && item.type === "contest" ? [item.id] : [],
        )
      : [],
  );
  const id = output.template === "results" ? output.evidence?.upload : null;
  return {
    number,
    summary:
      typeof found.draft?.summary === "string" ? found.draft.summary : "",
    workspace: output,
    records: looked,
    sheets: Object.fromEntries(
      await Promise.all(
        [...contests].map(
          async (contest) => [contest, await sheet(contest)] as const,
        ),
      ),
    ),
    upload: id == null ? null : await upload(id, locals.user.id),
    check: await latest(number, "check"),
  };
};
