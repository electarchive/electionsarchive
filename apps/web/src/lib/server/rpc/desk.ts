import { openapi } from "@orpc/openapi";
import { os as base, ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import {
  decide,
  prereview,
  proposal,
  queue,
  revert,
  roles,
} from "#lib/server/contrib.js";
import { db } from "#lib/server/db/index.js";
import { keyOwner, Number_, run, Text } from "#lib/server/rpc/keyed.js";

const os = base
  .$context<{ headers: Headers }>()
  .use(async ({ context, next }) => {
    const owner = await keyOwner(context.headers);
    if (owner == null) {
      throw new ORPCError("UNAUTHORIZED");
    }
    const allowed = await roles(owner.userId);
    if (!(allowed.admin || allowed.reviewer)) {
      throw new ORPCError("FORBIDDEN");
    }
    return next({ context: { userId: owner.userId } });
  });

const Reason = Text(2000);

export const admin = {
  queue: os
    .meta(
      openapi({
        method: "GET",
        path: "/admin/queue",
        summary:
          "Open and approved proposals, by tier then age, with prescreen verdicts, pre-review recommendations, and escalations (reviewer key only)",
      }),
    )
    .handler(async () => queue()),
  proposal: os
    .meta(
      openapi({
        method: "GET",
        path: "/admin/proposals/{number}",
        summary:
          "One proposal with its revisions, diffs, checks, prescreen, pre-review, escalations, and the stored files behind its evidence (reviewer key only)",
      }),
    )
    .input(v.object({ number: Number_ }))
    .handler(async ({ input }) =>
      run(async () => {
        const found = await proposal(Number(input.number));
        const { rows: files } = await db.execute<{
          key: string;
          kind: "capture" | "upload";
          url: string | null;
          media_type: string | null;
          storage_key: string | null;
          sha256: string | null;
          pages: number | null;
        }>(sql`
          select e.key, 'capture' as kind, a.final_url as url, a.media_type, a.raw_key as storage_key,
                 a.raw_sha256 as sha256, null::int as pages
          from proposal_revision r
          cross join jsonb_each(r.payload -> 'evidence') e
          join archive_capture a on a.id::text = e.value ->> 'capture'
          where r.proposal_id = ${found.id} and r.changeset_id = ${found.head_changeset_id}
          union all
          select e.key, 'upload', null, u.media_type, u.storage_key, u.sha256, u.pages
          from proposal_revision r
          cross join jsonb_each(r.payload -> 'evidence') e
          join upload u on u.id::text = e.value ->> 'upload'
          where r.proposal_id = ${found.id} and r.changeset_id = ${found.head_changeset_id}
        `);
        return { ...found, files };
      }),
    ),
  decide: os
    .meta(
      openapi({
        method: "POST",
        path: "/admin/proposals/{number}/decision",
        summary:
          "Approve, request changes on, or reject a proposal with a public reason; a reviewer approves only tier 1 proposals with no escalations (reviewer key only)",
      }),
    )
    .input(
      v.object({
        number: Number_,
        verb: v.picklist(["approve", "request_changes", "reject"]),
        reason: Reason,
      }),
    )
    .handler(async ({ input, context }) =>
      run(async () => {
        await decide({
          userId: context.userId,
          number: Number(input.number),
          verb: input.verb,
          reason: input.reason,
        });
        return { ok: true };
      }),
    ),
  prereview: os
    .meta(
      openapi({
        method: "POST",
        path: "/admin/proposals/{number}/prereview",
        summary:
          "Write the desk's pre-review of an open proposal's current revision: a summary, each fact checked, the risks, and a recommendation (reviewer key only)",
      }),
    )
    .input(
      v.object({
        number: Number_,
        summary: Text(4000),
        facts: v.pipe(
          v.array(
            v.object({
              fact: Text(1000),
              status: v.picklist(["verified", "unverified", "contradicted"]),
              note: v.optional(Text(2000)),
            }),
          ),
          v.maxLength(2000),
        ),
        risks: v.pipe(v.array(Text(1000)), v.maxLength(50)),
        recommendation: v.picklist(["approve", "request_changes", "reject"]),
      }),
    )
    .handler(async ({ input: { number, ...review }, context }) =>
      run(async () =>
        prereview({ userId: context.userId, number: Number(number), review }),
      ),
    ),
  revert: os
    .meta(
      openapi({
        method: "POST",
        path: "/admin/proposals/{number}/revert",
        summary:
          "Queue a revert of a published proposal with a public reason; a reviewer reverts only tier 1 proposals (reviewer key only)",
      }),
    )
    .input(v.object({ number: Number_, reason: Reason }))
    .handler(async ({ input, context }) =>
      run(async () => {
        await revert({
          userId: context.userId,
          number: Number(input.number),
          reason: input.reason,
        });
        return { ok: true };
      }),
    ),
};
