import type {
  LiveElection,
  LiveEstimate,
  LiveOffice,
  LiveRace,
} from "@ea/analytics/live";
import type { Summary } from "#lib/live/summary.js";
import type {
  BallotContest,
  BallotParty,
  BallotTier,
} from "#lib/server/live-ballot.js";
import { summarize } from "#lib/live/summary.js";
import { swatch } from "#lib/party.js";

export type Holder = { id: string; short: string; name: string; color: string };

export type Entry = {
  contest: BallotContest;
  summary: Summary;
  held: Holder | null;
  side: string | null;
  isLive: boolean;
  isFlip: boolean;
  isUnopposed: boolean;
};

const OFFICES: Partial<Record<BallotTier, LiveOffice>> = {
  senate: "senate",
  governor: "governor",
  house: "house",
};

export function holder(
  parties: Record<string, BallotParty>,
  id: string | null,
): Holder | null {
  if (id == null) {
    return null;
  }
  const found = parties[id];
  return found == null
    ? null
    : {
        id,
        short: found.short,
        name: found.name,
        color: swatch(found.color, `party:${id}`),
      };
}

export function stub(
  contest: BallotContest,
  state: string,
  parties: Record<string, BallotParty>,
  office: LiveOffice = "other",
  jurisdiction: LiveRace["jurisdiction"] = null,
): LiveRace {
  return {
    id: contest.id,
    slug: contest.slug,
    title: contest.title,
    office,
    state,
    district: office === "house" ? contest.seat : null,
    options: contest.options.map((option) => {
      const party = option.party == null ? null : parties[option.party];
      return {
        id: option.id,
        label: option.name,
        party:
          option.party == null || party == null
            ? null
            : {
                id: option.party,
                name: party.name,
                short: party.short,
                color: party.color ?? "",
              },
        isWriteIn: option.isWriteIn,
        isIncumbent: option.isIncumbent,
      };
    }),
    units: [],
    frames: [],
    counts: [],
    baseline: [],
    certified: null,
    source: { publisher: "", url: null },
    jurisdiction,
    seats: contest.seats,
    isMeasure: contest.isMeasure,
  };
}

export function entries({
  contests,
  election,
  estimates,
  parties,
  families,
  state,
  tier,
  jurisdiction = null,
}: {
  contests: Array<BallotContest>;
  election: LiveElection;
  estimates: Record<string, LiveEstimate>;
  parties: Record<string, BallotParty>;
  families: Record<string, string>;
  state: string;
  tier: BallotTier | null;
  jurisdiction?: LiveRace["jurisdiction"];
}): Array<Entry> {
  const live = new Map(election.races.map((race) => [race.slug, race]));
  return contests.map((contest) => {
    const found = live.get(contest.slug);
    const race =
      found ??
      stub(
        contest,
        state,
        parties,
        tier == null ? "other" : OFFICES[tier],
        jurisdiction,
      );
    const summary = summarize(race, estimates[race.id] ?? null);
    const held = holder(parties, contest.held);
    const pick = summary.winner ?? summary.leader;
    const side =
      pick?.party == null ? null : (families[pick.party.id] ?? pick.party.id);
    return {
      contest,
      summary,
      held,
      isLive: found != null,
      side,
      isFlip:
        held != null && side != null && summary.total > 0 && side !== held.id,
      isUnopposed:
        !contest.isMeasure &&
        race.options.filter((option) => !option.isWriteIn).length <=
          contest.seats,
    };
  });
}

export function district(entry: Entry) {
  const seat = entry.contest.seat;
  return seat == null ? Infinity : Number(seat) || 0;
}

export type Side = Holder & {
  before: number;
  won: number;
  ahead: number;
  holdover: number;
  waiting: number;
};

export function seating(
  entries: Array<Entry>,
  holdovers: Record<string, number>,
  parties: Record<string, BallotParty>,
) {
  const found = new Map<string, Side>();
  const take = (id: string) => {
    const known = found.get(id);
    if (known != null) {
      return known;
    }
    const party = holder(parties, id);
    const side: Side = {
      id,
      short: party?.short ?? "–",
      name: party?.name ?? "",
      color: party?.color ?? swatch(null, `party:${id}`),
      before: 0,
      won: 0,
      ahead: 0,
      holdover: 0,
      waiting: 0,
    };
    found.set(id, side);
    return side;
  };
  let total = 0;
  let waiting = 0;
  for (const [id, count] of Object.entries(holdovers)) {
    const side = take(id);
    side.holdover += count;
    side.before += count;
    total += count;
  }
  for (const entry of entries) {
    const seats = entry.contest.seats;
    total += seats;
    if (entry.held != null) {
      take(entry.held.id).before += seats;
    }
    if (entry.side == null || entry.summary.total === 0) {
      if (entry.held == null) {
        waiting += seats;
      } else {
        take(entry.held.id).waiting += seats;
      }
    } else if (entry.summary.winner == null) {
      take(entry.side).ahead += seats;
    } else {
      take(entry.side).won += seats;
    }
  }
  const sides = [...found.values()]
    .filter((side) => side.before + side.won + side.ahead > 0)
    .toSorted(
      (a, b) => b.before + b.won + b.ahead - (a.before + a.won + a.ahead),
    );
  const top = sides
    .slice(0, 2)
    .toSorted(
      (a, b) =>
        Number(/republican/iu.test(a.name)) -
        Number(/republican/iu.test(b.name)),
    );
  const [left, right] = top;
  const rest = sides.slice(2);
  return {
    total,
    majority: Math.floor(total / 2) + 1,
    waiting,
    isCounting: entries.some((entry) => entry.summary.total > 0),
    left: left ?? null,
    right: right ?? null,
    rest,
  };
}

export type Seating = ReturnType<typeof seating>;
