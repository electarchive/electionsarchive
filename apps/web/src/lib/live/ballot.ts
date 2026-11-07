import type {
  LiveElection,
  LiveEstimate,
  LiveOffice,
  LiveRace,
} from "@ea/analytics/live";
import type { Shift } from "#lib/live/flips.js";
import type { Summary } from "#lib/live/summary.js";
import type {
  BallotContest,
  BallotHoldover,
  BallotParty,
  BallotTier,
} from "#lib/server/live-ballot.js";
import { hatch, shift } from "#lib/live/flips.js";
import { tone } from "#lib/live/format.js";
import { summarize } from "#lib/live/summary.js";
import { m } from "#lib/paraglide/messages.js";
import { side, swatch, UNAFFILIATED } from "#lib/party.js";

export type Holder = { id: string; short: string; name: string; color: string };

export type Entry = {
  contest: BallotContest;
  summary: Summary;
  held: Holder | null;
  side: string | null;
  isLive: boolean;
  isFlip: boolean;
  shift: Shift | null;
  isUnopposed: boolean;
  unopposed: Array<string | null>;
  people: Array<string | null>;
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
  feed: LiveRace["feed"] = "live",
): LiveRace {
  const loose = contest.options.some((option) => option.party != null)
    ? []
    : contest.options.filter(
        (option) =>
          !option.isWriteIn &&
          (!contest.isMeasure ||
            side(`answer:${option.name.toLowerCase()}`) == null),
      );
  return {
    id: contest.id,
    slug: contest.slug,
    title: contest.title,
    office,
    state,
    district: office === "house" ? contest.seat : null,
    options: contest.options.map((option) => {
      const party = option.party == null ? null : parties[option.party];
      const place = loose.indexOf(option);
      return {
        id: option.id,
        label: option.name,
        person: option.person,
        photo: option.photo,
        ...(place !== -1 && {
          color: UNAFFILIATED[place % UNAFFILIATED.length],
        }),
        party:
          option.party == null || party == null
            ? null
            : {
                id: option.party,
                name: party.name,
                short: party.short,
                color: party.color ?? "",
              },
        ...(contest.isMeasure && !option.isWriteIn && { isAnswer: true }),
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
    feed,
    jurisdiction,
    seats: contest.seats,
    isMeasure: contest.isMeasure,
    rule: contest.rule ?? null,
    unopposed: contest.unopposed ?? null,
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
        election.later?.[state]?.feed,
      );
    const summary = summarize(race, estimates[race.id] ?? null);
    const held = holder(parties, contest.held);
    const familyOf = (party: string | undefined) =>
      party == null ? null : (families[party] ?? party);
    const pick = summary.winner ?? summary.unopposed ?? summary.leader;
    const unopposed =
      summary.unopposed == null
        ? []
        : race.options.filter((option) =>
            race.unopposed?.options.includes(option.id),
          );
    const side = familyOf(pick?.party?.id);
    const changing =
      contest.isMeasure || summary.isShort
        ? null
        : shift({
            held: held?.id ?? null,
            to: side,
            isCalled: summary.settled != null,
            isCounting: summary.total > 0,
          });
    return {
      contest,
      summary,
      held,
      isLive: found != null,
      side,
      isFlip: changing?.flip === "flipped",
      shift: changing,
      isUnopposed: race.unopposed != null,
      unopposed: unopposed.map((option) => familyOf(option.party?.id)),
      people: unopposed.map((option) => option.person),
    };
  });
}

export type Turn = {
  flip: "flipped" | "leading";
  to: { short: string; color: string };
};

export function turn(entry: Entry): Turn | null {
  const found = entry.shift;
  const { summary } = entry;
  const pick = summary.settled ?? summary.leader;
  if (found == null || found.flip === "held" || pick == null) {
    return null;
  }
  return {
    flip: found.flip,
    to: { short: pick.party?.short ?? "", color: tone(pick) },
  };
}

export function entryHatch(entry: Entry) {
  const found = turn(entry);
  return found == null || entry.summary.later != null
    ? null
    : hatch(found.to.color, found.flip);
}

export function district(entry: Entry) {
  const seat = entry.contest.seat;
  return seat == null ? Infinity : Number(seat) || 0;
}

export type Side = Holder & {
  members: Map<string, number>;
  makeup: string | null;
  before: number;
  won: number;
  ahead: number;
  holdover: number;
  waiting: number;
  gainedWon: number;
  gainedAhead: number;
};

export function seating(
  entries: Array<Entry>,
  holdovers: Array<BallotHoldover>,
  parties: Record<string, BallotParty>,
  caucuses: Record<string, string> = {},
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
      short: party?.short ?? (id === "" ? m.night_state_independent() : "–"),
      name: party?.name ?? (id === "" ? m.night_no_party() : ""),
      color: party?.color ?? swatch(null, `party:${id}`),
      members: new Map(),
      makeup: null,
      before: 0,
      won: 0,
      ahead: 0,
      holdover: 0,
      waiting: 0,
      gainedWon: 0,
      gainedAhead: 0,
    };
    found.set(id, side);
    return side;
  };
  const sit = (caucus: string, party: string, count: number) => {
    const side = take(caucus);
    side.before += count;
    side.members.set(party, (side.members.get(party) ?? 0) + count);
    return side;
  };
  const isSeated =
    holdovers.length > 0 ||
    entries.some((entry) => entry.contest.sitting.length > 0);
  const blocOf = (person: string | null | undefined, family: string | null) =>
    (person == null ? null : caucuses[person]) ?? family;
  let total = 0;
  let waiting = 0;
  for (const holdover of holdovers) {
    sit(holdover.caucus, holdover.party, holdover.seats).holdover +=
      holdover.seats;
    total += holdover.seats;
  }
  for (const entry of entries) {
    const seats = entry.contest.seats;
    total += seats;
    for (const sitter of entry.contest.sitting) {
      sit(sitter.caucus, sitter.party, 1);
    }
    if (!isSeated && entry.held != null) {
      sit(entry.held.id, entry.held.id, seats);
    }
    const held = entry.contest.sitting[0]?.caucus ?? entry.held?.id ?? null;
    const hold = (count: number) => {
      if (held == null) {
        waiting += count;
      } else {
        take(held).waiting += count;
      }
    };
    if (entry.unopposed.length > 0) {
      for (const [index, family] of entry.unopposed.entries()) {
        const bloc = blocOf(entry.people[index], family) ?? "";
        const side = take(bloc);
        side.won += 1;
        side.gainedWon += held == null || bloc === held ? 0 : 1;
      }
      hold(seats - entry.unopposed.length);
    } else if (
      entry.side == null ||
      entry.summary.total === 0 ||
      entry.summary.isShort
    ) {
      hold(seats);
    } else {
      const pick = entry.summary.winner ?? entry.summary.leader;
      const side = take(blocOf(pick?.person, entry.side) ?? entry.side);
      const isGain = entry.shift != null && entry.shift.flip !== "held";
      if (entry.summary.winner == null) {
        side.ahead += seats;
        side.gainedAhead += isGain ? seats : 0;
      } else {
        side.won += seats;
        side.gainedWon += isGain ? seats : 0;
      }
    }
  }
  for (const side of found.values()) {
    side.makeup = makeup(side, parties);
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
    isCounting: entries.some(
      (entry) => entry.summary.total > 0 || entry.unopposed.length > 0,
    ),
    left: left ?? null,
    right: right ?? null,
    rest,
  };
}

function makeup(side: Side, parties: Record<string, BallotParty>) {
  const others = [...side.members].filter(([party]) => party !== side.id);
  if (others.length === 0) {
    return null;
  }
  if (side.members.has(side.id)) {
    let count = 0;
    for (const [, seats] of others) {
      count += seats;
    }
    return others.every(([party]) => party === "")
      ? m.night_caucus_includes({ count })
      : m.night_caucus_includes_others({ count });
  }
  return [...side.members]
    .toSorted((a, b) => b[1] - a[1])
    .map(([party, count]) =>
      m.night_state_caucus_member({
        party:
          party === ""
            ? m.night_state_independent()
            : (parties[party]?.short ?? ""),
        count,
      }),
    )
    .join(" · ");
}

export type Seating = ReturnType<typeof seating>;
