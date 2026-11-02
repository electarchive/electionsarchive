import { openapi } from "@orpc/openapi";
import { error } from "@sveltejs/kit";
import * as v from "valibot";
import {
  BountyFilters,
  claim,
  get,
  list,
  normalize,
  release,
} from "#lib/server/bounties.js";
import { Number_, reader, run, writer } from "#lib/server/rpc/keyed.js";

export const bounties = {
  list: reader
    .meta(
      openapi({
        method: "GET",
        path: "/bounties",
        summary:
          "Bounties: known coverage gaps open for contributors to fill, 50 a page by number, each with its reason, the sources already tried, the date checked, who holds a claim, and who filled it; filter by polity code, grain, category (known_gap: no free source found; blocked: a source exists but a script cannot reach it), a year the gap covers, or status (open, fulfilled, all), and pass after=next for more",
      }),
    )
    .input(BountyFilters)
    .handler(async ({ input }) => run(async () => list(normalize(input)))),
  get: reader
    .meta(
      openapi({
        method: "GET",
        path: "/bounties/{number}",
        summary:
          "One bounty by number, with its reason, the sources tried, its claim, and its credit",
      }),
    )
    .input(v.object({ number: Number_ }))
    .handler(async ({ input }) =>
      run(async () => {
        const found = await get(Number(input.number));
        if (found == null) {
          error(404, "There is no bounty with that number.");
        }
        return found;
      }),
    ),
};

export const claims = {
  claim: writer
    .meta(
      openapi({
        method: "POST",
        path: "/bounties/{number}/claim",
        summary:
          "Claim an open bounty for seven days, or renew your own claim, so others know you're filling it; then pass its number as bounty when you draft the proposal, and publishing that proposal credits you",
      }),
    )
    .input(v.object({ number: Number_ }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => ({
        number: Number(input.number),
        ...(await claim(owner.userId, Number(input.number))),
      })),
    ),
  release: writer
    .meta(
      openapi({
        method: "DELETE",
        path: "/bounties/{number}/claim",
        summary: "Release your claim on a bounty",
      }),
    )
    .input(v.object({ number: Number_ }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        await release(owner.userId, Number(input.number));
        return { number: Number(input.number), claimed: false };
      }),
    ),
};
