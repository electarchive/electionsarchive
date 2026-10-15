import type { Lists } from "./rules.ts";

export type Candidate = {
  id: string;
  rank?: number | null | undefined;
  votes?: number | null | undefined;
};
export type Slate = {
  id: string;
  seats: number;
  share: number;
  candidates: Array<Candidate>;
};
export type Filled = {
  elected: Array<string>;
  short: number;
  tie?: Array<string> | undefined;
};

const byRank = (a: Candidate, b: Candidate) =>
  (a.rank ?? Number.POSITIVE_INFINITY) - (b.rank ?? Number.POSITIVE_INFINITY);
const byVotes = (a: Candidate, b: Candidate) =>
  (b.votes ?? 0) - (a.votes ?? 0) || byRank(a, b);

export function fill(
  lists: Lists,
  seats: number,
  candidates: Array<Candidate>,
  skip: ReadonlySet<string> = new Set(),
  quotient = 0,
): Filled {
  const floor = (lists.individual ?? 0) * quotient;
  const eligible = candidates.filter(
    ({ id, votes }) => !skip.has(id) && (votes ?? 0) >= floor,
  );
  let ordered: Array<Candidate>;
  switch (lists.order) {
    case "closed": {
      ordered = eligible.toSorted(byRank);
      break;
    }
    case "open": {
      ordered = eligible.toSorted(byVotes);
      break;
    }
    case "flexible": {
      const reach = (lists.quota ?? 1) * quotient;
      const preferred = eligible
        .filter(({ votes }) => (votes ?? 0) >= reach)
        .toSorted(byVotes);
      const chosen = new Set(preferred);
      ordered = [
        ...preferred,
        ...eligible
          .filter((candidate) => !chosen.has(candidate))
          .toSorted(byRank),
      ];
    }
  }
  const last = ordered[seats - 1];
  const tied =
    lists.order === "open" &&
    last != null &&
    ordered[seats]?.votes === last.votes
      ? ordered.filter(({ votes }) => votes === last.votes)
      : [];
  return {
    elected: ordered
      .slice(0, seats)
      .filter((candidate) => !tied.includes(candidate))
      .map(({ id }) => id),
    short: Math.max(0, seats - ordered.length),
    tie: tied.length > 0 ? tied.map(({ id }) => id) : undefined,
  };
}

export function cover(
  constituencies: "all" | "covered",
  seats: number,
  winners: Array<{ id: string; share: number }>,
) {
  if (constituencies === "all")
    return {
      covered: winners.map(({ id }) => id),
      overhang: Math.max(0, winners.length - seats),
      tie: undefined,
    };
  const sorted = winners.toSorted((a, b) => b.share - a.share);
  const last = sorted[seats - 1];
  const isTied = last != null && sorted[seats]?.share === last.share;
  return {
    covered: sorted
      .slice(0, seats)
      .filter(({ share }) => !isTied || share !== last.share)
      .map(({ id }) => id),
    overhang: 0,
    tie: isTied
      ? sorted.filter(({ share }) => share === last.share).map(({ id }) => id)
      : undefined,
  };
}

export function proclaim(
  lists: Lists,
  slates: Array<Slate>,
  skip: ReadonlySet<string> = new Set(),
): Map<string, Filled> {
  const excluded = new Map<string, Set<string>>();
  for (;;) {
    const results = new Map<string, Filled>();
    const held = new Map<string, Array<Slate>>();
    for (const slate of slates) {
      const result = fill(
        lists,
        slate.seats,
        slate.candidates,
        new Set([...skip, ...(excluded.get(slate.id) ?? [])]),
      );
      results.set(slate.id, result);
      for (const id of result.elected)
        held.set(id, [...(held.get(id) ?? []), slate]);
    }
    let isSettled = true;
    for (const [id, among] of held) {
      if (among.length < 2) continue;
      if (lists.multiple == null)
        throw new Error(`${id} is elected on several lists`);
      const kept = among.toSorted((a, b) => a.share - b.share)[0];
      for (const slate of among) {
        if (slate === kept) continue;
        excluded.set(
          slate.id,
          new Set([...(excluded.get(slate.id) ?? []), id]),
        );
        isSettled = false;
      }
    }
    if (isSettled) return results;
  }
}
