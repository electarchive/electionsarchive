import type { archive } from "#lib/server/pages.js";
import { count, definite, percent, points, precision } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";
import { getLocale } from "#lib/paraglide/runtime.js";

type Page = Extract<Awaited<ReturnType<typeof archive>>, { kind: "contest" }>;

const named = (row: {
  label: string;
  short: string | null;
  person?: string | null;
  current?: { person: string | null } | null;
}) =>
  row.label.length > 28 && (row.person ?? row.current?.person) == null
    ? (row.short ?? row.label)
    : row.label;

export function isQuestion(contest: Page["contest"]) {
  return contest.rows.some((row) => row.key.startsWith("answer:"));
}

export function isSeated(contest: Page["contest"]) {
  let total = 0;
  for (const row of contest.rows) {
    total += row.current?.seats ?? 0;
  }
  return (
    total > 1 &&
    contest.rows.some(
      (row) => (row.current?.seats ?? 0) > 0 && row.current?.person == null,
    )
  );
}

export function winners(contest: Page["contest"]) {
  const seated = contest.rows.filter((row) => (row.current?.seats ?? 0) > 0);
  const won =
    seated.length === 0 && contest.isStv
      ? contest.rows.filter((row) => row.current?.outcome === "elected")
      : seated;
  return won.length > 1 &&
    won.every(
      (row) => row.current?.person != null && (row.current.seats ?? 1) === 1,
    )
    ? won
    : null;
}

export function verdict(contest: Page["contest"]) {
  const isAsked = isQuestion(contest);
  const isSeat = isSeated(contest);
  const members = winners(contest);
  if (contest.current == null) {
    return null;
  }
  if (isAsked) {
    const top = contest.rows
      .filter((row) => row.current != null)
      .toSorted((a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0))[0];
    const next = contest.rows
      .filter((row) => row.current != null && row !== top)
      .toSorted((a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0))[0];
    const yes = /^(?:yes|for|approve|(?:sì|si|oui|ja|evet|sim)(?!\p{L}))/iu;
    const turned =
      contest.turnout == null || contest.turnout.registered === 0
        ? null
        : contest.turnout.voters / contest.turnout.registered;
    return contest.quorum != null &&
      turned != null &&
      top?.current?.outcome === "lost" &&
      yes.test(top.label) &&
      turned <= contest.quorum
      ? m.race_lede_quorum({
          turnout: percent(turned),
          quorum: percent(contest.quorum),
          share: percent(top.current.share),
        })
      : top?.current?.outcome === "lost" && yes.test(top.label)
        ? m.race_lede_measure_blocked({ share: percent(top.current.share) })
        : top?.current?.outcome === "lost"
          ? m.race_lede_lost({
              answer: named(top),
              share: percent(top.current.share),
            })
          : top?.current?.outcome === "won" &&
              next?.current != null &&
              yes.test(top.label)
            ? m.race_lede_measure_passed({ share: percent(top.current.share) })
            : top?.current?.outcome === "won" &&
                next?.current != null &&
                /^(?:no|against|reject|nein|hayır|não)/iu.test(top.label)
              ? m.race_lede_measure_failed({
                  share: percent(top.current.share),
                })
              : top?.current?.outcome === "won" && next?.current != null
                ? m.race_lede_won({
                    party: named(top),
                    share: percent(
                      top.current.share,
                      precision(top.current.share - next.current.share),
                    ),
                    margin: points(
                      top.current.share - next.current.share,
                    ).replace(/^\+/u, ""),
                    runner: named(next),
                  })
                : null;
  }
  const [elector, second] = contest.electors?.rows ?? [];
  if (elector != null) {
    let total = contest.electors?.appointed ?? 0;
    if (contest.electors?.appointed == null) {
      const listed = contest.electors?.rows ?? [];
      for (const row of listed) {
        total += row.votes;
      }
    }
    const popular = contest.rows.find(
      (row) =>
        row.current?.person != null && row.current.person === elector.person,
    );
    return second?.votes === elector.votes
      ? m.race_lede_electors_tied({
          party: definite(elector.label, true),
          runner: definite(second.label),
          seats: count(elector.votes),
          total: count(total),
        })
      : popular?.current == null
        ? m.race_lede_electors_only({
            party: definite(elector.label, true),
            seats: count(elector.votes),
            total: count(total),
          })
        : m.race_lede_electors({
            party: definite(elector.label, true),
            seats: count(elector.votes),
            total: count(total),
            share: percent(
              popular.current.share,
              precision(
                popular.current.share -
                  Math.max(
                    0,
                    ...contest.rows.flatMap((row) =>
                      row === popular || row.key.startsWith("kind:")
                        ? []
                        : [row.current?.share ?? 0],
                    ),
                  ),
              ),
            ),
          });
  }
  if (isSeat || (members != null && contest.isStv)) {
    let total = 0;
    for (const row of contest.rows) {
      total += row.current?.seats ?? 0;
    }
    if (total === 0) {
      total = members?.length ?? 0;
    }
    if (members != null && (members.length <= 4 || contest.isStv)) {
      const list = (rows: typeof members) =>
        new Intl.ListFormat(getLocale(), {
          style: "long",
          type: "conjunction",
        }).format(
          rows.map((row) => {
            const party =
              row.short ??
              (row.current?.isIndependent === true
                ? m.seats_independents()
                : null);
            return party == null
              ? row.label
              : m.race_member({ name: row.label, party });
          }),
        );
      const returned =
        contest.isStv && contest.count != null
          ? members.filter((row) => (row.current?.votes ?? 0) === 0)
          : [];
      if (returned.length === 1 && members.length > 1) {
        return m.race_lede_members_stv_returned({
          names: list(members.filter((row) => !returned.includes(row))),
          seats: count(members.length - returned.length),
          total: count(total),
          returned: list(returned),
        });
      }
      const names = list(members);
      return contest.isStv && contest.count == null
        ? m.race_lede_members_stv_first({
            names,
            total: count(total),
            quota: count(Math.floor(contest.current.valid / (total + 1)) + 1),
          })
        : (contest.isStv ? m.race_lede_members_stv : m.race_lede_members)({
            names,
            total: count(total),
          });
    }
    const blocs = Map.groupBy(
      contest.rows.filter((row) => row.current != null),
      (row) =>
        contest.partners.find((pair) => pair.includes(row.key))?.[0] ?? row.key,
    )
      .values()
      .map((members) => {
        let seats = 0;
        let share = 0;
        for (const row of members) {
          seats += row.current?.seats ?? 0;
          share += row.current?.share ?? 0;
        }
        return { names: members.map((row) => named(row)), seats, share };
      });
    const top = blocs.toArray().toSorted((a, b) => b.seats - a.seats)[0];
    return top == null
      ? null
      : (contest.kind === "office" || contest.kind === "elector_slate"
          ? m.race_lede_electors
          : m.race_lede_seats)({
          party: definite(top.names.join("/"), true),
          seats: count(top.seats),
          total: count(total),
          share: percent(top.share),
        });
  }
  if (contest.count != null) {
    const last = contest.count.last - 1;
    const [first, second] = contest.count.rows;
    const won = contest.rows.find(
      (row) =>
        row.key === first?.key ||
        (first?.person != null && row.current?.person === first.person),
    )?.current;
    const total = (first?.votes[last] ?? 0) + (second?.votes[last] ?? 0);
    return first == null || second == null || total === 0
      ? null
      : (["won", "elected"].includes(won?.outcome ?? "")
          ? m.race_lede_final
          : m.race_lede_final_led)({
          party: definite(named(first), true),
          share: percent((first.votes[last] ?? 0) / total),
          margin: count(
            Math.round(
              (((first.votes[last] ?? 0) - (second.votes[last] ?? 0)) / total) *
                1000,
            ) / 10,
          ),
          runner: definite(named(second)),
        });
  }
  const [top, next] = contest.rows
    .filter((row) => row.current != null && !row.key.startsWith("kind:"))
    .toSorted((a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0));
  if (next == null && top?.current != null) {
    return (
      ["won", "elected"].includes(top.current.outcome ?? "")
        ? m.race_lede_unopposed
        : m.race_lede_unopposed_led
    )({ party: definite(named(top), true) });
  }
  if (top?.current == null || next?.current == null) {
    return null;
  }
  const isWon = ["won", "elected"].includes(top.current.outcome ?? "");
  const withdrew = contest.rows.find(
    (row) => row.current?.outcome === "withdrew",
  );
  const lede = (
    isWon && withdrew?.key === next.key
      ? m.race_lede_won_withdrew
      : isWon
        ? m.race_lede_won
        : m.race_lede_votes
  )({
    party: definite(named(top), true),
    share: percent(
      top.current.share,
      precision(top.current.share - next.current.share),
    ),
    margin: points(top.current.share - next.current.share).replace(/^\+/u, ""),
    runner: named(next),
  });
  return isWon && withdrew != null && withdrew.key !== next.key
    ? `${lede} ${m.race_lede_withdrew({ name: named(withdrew) })}`
    : lede;
}

export function comeback(contest: Page["contest"], rounds: Page["rounds"]) {
  if (contest.current == null || isQuestion(contest)) {
    return null;
  }
  const winner = contest.rows.find((row) =>
    ["won", "elected"].includes(row.current?.outcome ?? ""),
  );
  const isSame = (key: string, person: string | null) =>
    winner != null &&
    (winner.key === key ||
      (person != null && winner.current?.person === person));
  if (contest.count != null) {
    let total = 0;
    for (const row of contest.count.rows) {
      total += row.votes[0] ?? 0;
    }
    const [leader] = contest.count.rows.toSorted(
      (a, b) => (b.votes[0] ?? 0) - (a.votes[0] ?? 0),
    );
    return leader == null || total === 0 || isSame(leader.key, leader.person)
      ? null
      : m.race_lede_first_led({
          name: leader.label,
          share: percent((leader.votes[0] ?? 0) / total),
        });
  }
  const index = rounds.findIndex((round) => round.isCurrent);
  const leader = rounds[index - 1]?.leaders[0];
  return leader == null ||
    rounds[index]?.kind !== "runoff" ||
    isSame(leader.key, leader.person)
    ? null
    : m.race_lede_first_led({
        name: leader.label,
        share: percent(leader.share),
      });
}
