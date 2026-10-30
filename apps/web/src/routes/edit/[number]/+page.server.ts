import { error, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { view } from "#lib/server/rpc/proposals.js";
import { latest, records, Workspace } from "#lib/server/workspace.js";
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
  return {
    number,
    summary:
      typeof found.draft?.summary === "string" ? found.draft.summary : "",
    workspace: workspace.output,
    records: await records(workspace.output.rows.map((row) => row.record)),
    check: await latest(number, "check"),
  };
};
