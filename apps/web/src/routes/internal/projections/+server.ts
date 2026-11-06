import { PROJECTION_TOKEN } from "$app/env/private";
import { error, json } from "@sveltejs/kit";
import { createHash, timingSafeEqual } from "node:crypto";
import { sum } from "#lib/live/standing.js";
import { remember } from "#lib/server/cache.js";
import { control } from "#lib/server/live-board.js";
import { projections } from "#lib/server/rpc/live.js";
import type { RequestHandler } from "./$types";

const RULE = { chance: 0.995, counted: 0.5 };

const digest = (text: string) => createHash("sha256").update(text).digest();

export const GET: RequestHandler = async ({ request, url }) => {
  const given = request.headers.get("authorization")?.replace(/^Bearer /u, "");
  if (
    PROJECTION_TOKEN == null ||
    given == null ||
    !timingSafeEqual(digest(given), digest(PROJECTION_TOKEN))
  ) {
    error(401, "Unauthorized");
  }
  const path = url.searchParams.get("path");
  if (path == null) {
    error(400, "Missing path");
  }
  const watch = new Set(url.searchParams.get("watch")?.split(","));
  const { election, estimates } = await projections(path);
  const held = await remember(
    path.split("/").length === 2
      ? `live:control:v4:${election.date}`
      : `live:holders:v4:${election.slug}`,
    async () => control(election.races, election.date),
  );
  const races = election.races.flatMap((race) => {
    const found = estimates[race.id];
    const frame = race.frames.at(-1);
    const totals = frame?.totals ?? race.options.map(() => 0);
    const total = sum(totals);
    const contenders = race.options.filter((option) => !option.isWriteIn);
    const isUncontested = contenders.length === 1;
    const leaderId = isUncontested
      ? contenders[0]?.id
      : found?.status === "estimating"
        ? found.leader
        : null;
    const chance =
      leaderId == null
        ? null
        : isUncontested
          ? 1
          : (found?.winProbability[leaderId] ?? null);
    const counted =
      found?.status === "estimating" ? Math.min(1, found.shareIn.mid) : null;
    const isCalled = race.projected != null;
    if (
      total === 0 ||
      (!watch.has(race.id) &&
        (race.certified != null ||
          (!isCalled && (chance == null || chance < 0.9))))
    ) {
      return [];
    }
    const lines = race.options
      .map((option, index) => ({
        id: option.id,
        name: option.label,
        party: option.party?.short ?? option.party?.name ?? "",
        color: option.party?.color ?? "",
        votes: totals[index] ?? 0,
      }))
      .toSorted((a, b) => b.votes - a.votes)
      .slice(0, 3);
    const expected =
      found?.status === "estimating" ? found.expectedTotal : null;
    const holder = held.holders[race.id];
    const party = holder?.party == null ? null : held.parties[holder.party];
    const pickId = race.projected?.option ?? leaderId;
    const pick = race.options.find((option) => option.id === pickId);
    const pickParty = pick?.party?.id;
    return [
      {
        id: race.id,
        slug: race.slug,
        title: race.title,
        office: race.office,
        state: race.state,
        district: race.district,
        leader: leaderId,
        chance,
        counted,
        margin:
          found?.status === "estimating"
            ? found.margin
            : { low: 1, mid: 1, high: 1 },
        total,
        remaining:
          expected == null
            ? null
            : {
                low: Math.max(0, expected.low - total),
                mid: Math.max(0, expected.mid - total),
                high: Math.max(0, expected.high - total),
              },
        reporting: frame?.reporting ?? null,
        expected: frame?.expected ?? null,
        updated: frame?.at ?? null,
        source: race.source,
        lines,
        called: race.projected ?? null,
        calledChance:
          race.projected != null && found?.status === "estimating"
            ? (found.winProbability[race.projected.option] ?? null)
            : null,
        isCertified: race.certified != null,
        winners: race.certified?.winners ?? null,
        isUncontested,
        held:
          holder?.party == null || party == null
            ? null
            : {
                party: party.short,
                color: party.color ?? "",
                isVacant: holder.isVacant,
              },
        isFlip:
          holder?.party == null || pick == null
            ? null
            : (pickParty == null
                ? null
                : (held.families[pickParty] ?? pickParty)) !== holder.party,
        isReady:
          !isCalled &&
          race.certified == null &&
          chance != null &&
          chance >= RULE.chance &&
          (isUncontested || (counted ?? 0) >= RULE.counted),
      },
    ];
  });
  return json({
    election: { slug: election.slug, title: election.title },
    rule: RULE,
    at: new Date().toISOString(),
    races,
  });
};
