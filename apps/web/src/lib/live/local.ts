import type { LiveOption, LiveRace } from "@ea/analytics/live";
import type { Summary } from "#lib/live/summary.js";

export type LocalKind =
  | "measure"
  | "county"
  | "city"
  | "school"
  | "judicial"
  | "special"
  | "other";

export type LocalSort = "close" | "counted" | "name";

export type Local = {
  summary: Summary;
  state: string;
  place: string;
  kind: LocalKind;
  seats: number;
  isMeasure: boolean;
  closeness: number;
  haystack: string;
};

export type LocalGroup = {
  key: string;
  state: string;
  place: string;
  locals: Array<Local>;
};

const MEASURE =
  /\b(?:proposal|proposition|measure|question|amendment|referendum|initiative|millage|bond|levy|charter|ordinance|recall)\b/iu;

const RULES: Array<[LocalKind, RegExp]> = [
  ["judicial", /\b(?:judge|justice|court|magistrate)\b/iu],
  ["school", /\b(?:school|education|college|isd|usd)\b/iu],
  [
    "special",
    /\b(?:library|water|fire|hospital|park|transit|sanitary|drain|soil|utility|port|cemetery|conservation)\b/iu,
  ],
  ["city", /\b(?:city|town|township|village|borough|municipal)\b/iu],
  [
    "county",
    /\b(?:county|parish|sheriff|commissioner|clerk|treasurer|prosecutor|coroner|assessor|register)\b/iu,
  ],
  ["city", /\b(?:mayor|council|alder\w*|selectboard)\b/iu],
  ["special", /\b(?:district|authority)\b/iu],
];

const PLACE =
  /^(.+?\b(?:County|Parish|City|Town|Township|Village|Borough|School District|Public Schools|Community Schools))\b/u;

const JURISDICTIONS: Record<
  NonNullable<LiveRace["jurisdiction"]>["kind"],
  LocalKind | null
> = {
  county: "county",
  municipal: "city",
  school: "school",
  special: "special",
  judicial: "judicial",
  state: null,
};

export function fold(text: string) {
  return text.toLowerCase().normalize("NFD").replaceAll(/\p{M}/gu, "");
}

export function tokenize(query: string) {
  return fold(query)
    .split(/\s+/u)
    .filter((token) => token !== "");
}

function isAffirmative(option: LiveOption) {
  return /\byes\b|^(?:for|approve)\b/iu.test(option.label.trim());
}

function isNegative(option: LiveOption) {
  return /\bno\b|^(?:against|reject)\b/iu.test(option.label.trim());
}

export function sides(race: LiveRace) {
  const yes = race.options.findIndex((option) => isAffirmative(option));
  const no = race.options.findIndex((option) => isNegative(option));
  return yes === -1 || no === -1 ? null : { yes, no };
}

function closeness(summary: Summary, seats: number) {
  if (summary.total === 0) {
    return Infinity;
  }
  const order = summary.totals
    .map((votes, index) => ({ votes, index }))
    .filter((entry) => summary.race.options[entry.index]?.isWriteIn !== true)
    .toSorted((a, b) => b.votes - a.votes);
  const inside = order[seats - 1]?.votes ?? 0;
  const outside = order[seats]?.votes ?? 0;
  return (inside - outside) / summary.total;
}

export function localize(summary: Summary, stateName: string): Local {
  const { race } = summary;
  const found = race.jurisdiction ?? null;
  const isMeasure =
    race.isMeasure ?? (sides(race) != null || MEASURE.test(race.title));
  let kind: LocalKind = "other";
  if (isMeasure) {
    kind = "measure";
  } else if (found != null && JURISDICTIONS[found.kind] != null) {
    kind = JURISDICTIONS[found.kind] ?? "other";
  } else {
    for (const [candidate, rule] of RULES) {
      if (rule.test(race.title)) {
        kind = candidate;
        break;
      }
    }
  }
  const seats =
    race.seats != null && race.seats > 0
      ? race.seats
      : Math.trunc(Number(/vote for (\d+)/iu.exec(race.title)?.[1] ?? "1"));
  const place = found?.name ?? PLACE.exec(race.title)?.[1] ?? stateName;
  return {
    summary,
    state: race.state,
    place,
    kind,
    seats,
    isMeasure,
    closeness: closeness(summary, isMeasure ? 1 : seats),
    haystack: fold(
      [
        race.title,
        place,
        stateName,
        ...race.options.flatMap((option) => [
          option.label,
          option.party?.name ?? "",
          option.party?.short ?? "",
        ]),
      ].join(" "),
    ),
  };
}

function compare(sort: LocalSort, a: Local, b: Local) {
  if (sort === "close") {
    return a.closeness - b.closeness;
  }
  if (sort === "counted") {
    return (b.summary.counted ?? -1) - (a.summary.counted ?? -1);
  }
  return 0;
}

export function arrange(
  locals: Array<Local>,
  tokens: Array<string>,
  kind: LocalKind | "all",
  sort: LocalSort,
) {
  const groups = new Map<string, LocalGroup>();
  for (const local of locals) {
    if (
      (kind !== "all" && local.kind !== kind) ||
      tokens.some((token) => !local.haystack.includes(token))
    ) {
      continue;
    }
    const key = `${local.state}:${local.place}`;
    const group = groups.get(key) ?? {
      key,
      state: local.state,
      place: local.place,
      locals: [],
    };
    group.locals.push(local);
    groups.set(key, group);
  }
  const found = [...groups.values()];
  for (const group of found) {
    group.locals.sort(
      (a, b) =>
        compare(sort, a, b) ||
        a.summary.race.title.localeCompare(b.summary.race.title),
    );
  }
  return found.toSorted((a, b) => {
    const first = a.locals[0];
    const second = b.locals[0];
    const ranked =
      first == null || second == null || sort === "name"
        ? 0
        : compare(sort, first, second);
    return ranked || a.place.localeCompare(b.place);
  });
}

export function highlight(text: string, tokens: Array<string>) {
  if (tokens.length === 0) {
    return [{ text, isHit: false }];
  }
  let folded = "";
  const starts: Array<number> = [];
  const ends: Array<number> = [];
  let offset = 0;
  for (const char of text) {
    const piece = fold(char);
    for (let unit = 0; unit < piece.length; unit += 1) {
      starts.push(offset);
      ends.push(offset + char.length);
    }
    folded += piece;
    offset += char.length;
  }
  const hits = new Set<number>();
  for (const token of tokens) {
    let at = folded.indexOf(token);
    while (at !== -1) {
      const from = starts[at] ?? 0;
      const to = ends[at + token.length - 1] ?? from;
      for (let index = from; index < to; index += 1) {
        hits.add(index);
      }
      at = folded.indexOf(token, at + token.length);
    }
  }
  const segments: Array<{ text: string; isHit: boolean }> = [];
  let start = 0;
  for (let index = 1; index <= text.length; index += 1) {
    if (index === text.length || hits.has(index) !== hits.has(start)) {
      segments.push({ text: text.slice(start, index), isHit: hits.has(start) });
      start = index;
    }
  }
  return segments;
}
