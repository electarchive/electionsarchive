<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import Name from "#lib/components/name.svelte";
  import Term from "#lib/components/term.svelte";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import {
    count,
    date,
    definite,
    percent,
    points,
    precision,
  } from "#lib/format.js";
  import { explain, isDerived, MEASURES, OUTCOMES } from "#lib/outcomes.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Additional from "./additional.svelte";
  import Ballot from "./ballot.svelte";
  import Changes from "./changes.svelte";
  import Count from "./count.svelte";
  import Cycle from "./cycle.svelte";
  import ElectionRow from "./election-row.svelte";
  import Electors from "./electors.svelte";
  import Lists from "./lists.svelte";
  import PlaceMap from "./map.svelte";
  import Members from "./members.svelte";
  import PlaceSwitcher from "./place-switcher.svelte";
  import Places from "./places.svelte";
  import Preview from "./preview.svelte";
  import Results from "./results.svelte";
  import Rounds from "./rounds.svelte";
  import Seats from "./seats.svelte";
  import Series from "./series.svelte";
  import Shift from "./shift.svelte";
  import Stv from "./stv.svelte";
  import Tally from "./tally.svelte";
  import Wording from "./wording.svelte";

  const {
    contest,
    lists,
    rounds,
    ballot,
    cycle,
    handover,
    additional,
    successors,
    boundaries,
    surround,
    match,
  }: {
    contest: Extract<PageData, { kind: "contest" }>["contest"];
    lists: Extract<PageData, { kind: "contest" }>["lists"];
    rounds: Extract<PageData, { kind: "contest" }>["rounds"];
    ballot: Extract<PageData, { kind: "contest" }>["ballot"];
    cycle: Extract<PageData, { kind: "contest" }>["cycle"];
    handover: Extract<PageData, { kind: "contest" }>["handover"];
    additional: Extract<PageData, { kind: "contest" }>["additional"];
    successors: Extract<PageData, { kind: "contest" }>["successors"];
    boundaries: Extract<PageData, { kind: "contest" }>["boundaries"];
    surround: Extract<PageData, { kind: "contest" }>["surround"];
    match: string;
  } = $props();

  const DAY: Record<string, () => string> = {
    owner: () => "",
    governor: m.cycle_governor,
    legislature: m.cycle_legislature,
    regional_office: m.cycle_regional_office,
    national_measure: m.cycle_national_measure,
    regional_measure: m.cycle_regional_measure,
    local: m.cycle_local,
    local_measure: m.cycle_local_measure,
  };
  const STAGES: Record<string, () => string> = {
    live: m.stage_live,
    preliminary: m.stage_preliminary,
    official: m.stage_official,
    certified: m.stage_certified,
    court_revised: m.stage_court_revised,
    recount: m.stage_recount,
    alternative: m.stage_alternative,
    unreconciled: m.stage_unreconciled,
    secondary: m.stage_secondary,
  };
  const DEFINITIONS: Record<string, () => string> = {
    list_votes: m.term_list_votes,
    electoral_votes: m.term_electoral_votes,
    state_delegate_equivalents: m.term_state_delegate_equivalents,
    live: m.term_stage_live,
    preliminary: m.term_stage_preliminary,
    official: m.term_stage_official,
    certified: m.term_stage_certified,
    court_revised: m.term_stage_court_revised,
    recount: m.term_stage_recount,
    alternative: m.term_stage_alternative,
    unreconciled: m.term_stage_unreconciled,
    secondary: m.term_stage_secondary,
  };
  const DECLARED: Record<string, () => string> = {
    tacit: m.declared_tacit,
    show_of_hands: m.declared_show_of_hands,
    appointed: m.declared_appointed,
    counted: m.declared_counted,
  };
  const STATUSES: Record<string, () => string> = {
    superseded: m.status_superseded,
    annulled: m.status_annulled,
    withdrawn: m.status_withdrawn,
  };
  const SEATS: Record<string, () => string> = {
    office: m.col_electors,
    elector_slate: m.col_electors,
    delegate_allocation: m.col_delegates,
  };

  const tether = TooltipPrimitive.createTether<string>();

  const single = $derived.by(() => {
    if (contest.chamber?.link == null) {
      return null;
    }
    const winners = contest.chamber.parties.filter((party) => party.here > 0);
    const [winner] = winners;
    if (winners.length !== 1 || winner?.here !== 1) {
      return null;
    }
    let total = 0;
    for (const party of contest.chamber.parties) {
      total += party.seats;
    }
    return {
      winner,
      party:
        /^(?:party:)?([0-9a-f]{8}-[0-9a-f-]{27})$/.exec(winner.key)?.[1] ??
        null,
      total: contest.chamber.size ?? total,
      link: contest.chamber.link,
    };
  });
  const chamber = $derived(single == null ? contest.chamber : null);

  const origins = $derived.by(() => {
    const list = contest.origins ?? [];
    const year = contest.series.find((entry) => entry.current)?.year;
    if (list.length === 0 || year == null) {
      return [];
    }
    const [only] = list;
    if (
      list.length === 1 &&
      only != null &&
      only.share >= 0.9 &&
      only.first != null &&
      only.year != null &&
      only.first < only.year
    ) {
      const [before = "", after = ""] = m
        .origins_one({ year, seat: "\u0001", from: only.first, to: only.year })
        .split("\u0001");
      return [
        { text: before, path: null },
        { text: only.name, path: only.path },
        { text: after, path: null },
      ];
    }
    const [before = "", after = ""] = m
      .origins_many({ year, seats: "\u0001" })
      .split("\u0001");
    const parts = new Intl.ListFormat(getLocale(), {
      type: "conjunction",
    }).formatToParts(list.map((_, index) => String(index)));
    return [
      { text: before, path: null },
      ...parts.flatMap((part) => {
        const origin =
          part.type === "element" ? list[Number(part.value)] : undefined;
        if (origin == null) {
          return [{ text: part.value, path: null }];
        }
        const [head = "", tail = ""] = m
          .origins_item({ name: "\u0001", share: percent(origin.share, 0) })
          .split("\u0001");
        return [
          { text: head, path: null },
          { text: origin.name, path: origin.path },
          { text: tail, path: null },
        ];
      }),
      { text: after, path: null },
    ];
  });

  const heirs = $derived.by(() => {
    if (successors == null) {
      return [];
    }
    const [only] = successors.places;
    if (successors.places.length === 1 && only != null && only.share >= 0.9) {
      const [before = "", after = ""] = m
        .successors_one({ year: successors.year, seat: "\u0001" })
        .split("\u0001");
      return [
        { text: before, path: null },
        { text: only.name, path: only.path },
        { text: after, path: null },
      ];
    }
    const [before = "", after = ""] = m
      .successors_many({ year: successors.year, seats: "\u0001" })
      .split("\u0001");
    const parts = new Intl.ListFormat(getLocale(), {
      type: "conjunction",
    }).formatToParts(successors.places.map((_, index) => String(index)));
    return [
      { text: before, path: null },
      ...parts.flatMap((part) => {
        const place =
          part.type === "element"
            ? successors.places[Number(part.value)]
            : undefined;
        if (place == null) {
          return [{ text: part.value, path: null }];
        }
        const [head = "", tail = ""] = m
          .origins_item({ name: "\u0001", share: percent(place.share, 0) })
          .split("\u0001");
        return [
          { text: head, path: null },
          { text: place.name, path: place.path },
          { text: tail, path: null },
        ];
      }),
      { text: after, path: null },
    ];
  });

  let filter = $state<{ at: string; value: number | string | null }>({
    at: "",
    value: null,
  });

  let focus = $state<number | null>(null);

  const here = $derived(page.url.pathname.slice(1));
  const upward = $derived.by(() => {
    const parent = contest.scope.trail.at(-2);
    return parent?.path == null
      ? null
      : { name: parent.name, path: parent.path };
  });
  const grouped = $derived.by(() => {
    const legend = contest.places?.legend ?? [];
    if (
      !(contest.places?.rows ?? []).some((row) =>
        legend[row.leader ?? -1]?.key.startsWith("kind:others"),
      )
    ) {
      return null;
    }
    const keys = new Set(legend.map((item) => item.key));
    const people = new Set(legend.map((item) => item.person));
    const absent = contest.rows.filter(
      (row) =>
        row.current != null &&
        !row.key.startsWith("kind:") &&
        !keys.has(row.key) &&
        !people.has(row.current.person),
    );
    let total = 0;
    for (const row of absent) {
      total += row.current?.votes ?? 0;
    }
    const top = absent.toSorted(
      (a, b) => (b.current?.votes ?? 0) - (a.current?.votes ?? 0),
    )[0];
    return top?.current == null || top.current.votes < total * 0.9 ? null : top;
  });
  const placed = $derived(
    contest.places == null ||
      (contest.places.rows.length > 0 &&
        contest.places.rows.every((row) => row.valid === 0))
      ? null
      : grouped == null
        ? contest.places
        : {
            ...contest.places,
            legend: contest.places.legend.map((item) =>
              item.key.startsWith("kind:others")
                ? {
                    ...item,
                    label: m.places_others_with({ name: grouped.label }),
                    color: grouped.color,
                    person: grouped.current?.person ?? null,
                    party: grouped.current?.party ?? null,
                  }
                : item,
            ),
          },
  );
  const drawn = $derived.by(() => {
    const places = placed;
    const level = boundaries.find((entry) => entry.scheme === places?.level);
    const own = boundaries.find(
      (entry) => entry.k != null && entry.scheme !== places?.level,
    );
    const inner = boundaries.find(
      (entry) =>
        entry.k == null &&
        entry.scheme !== places?.level &&
        entry.tiles != null,
    );
    const districts = places?.districts ?? [];
    const noun = places?.levels.find(
      (entry) => entry.code === places.level,
    )?.noun;
    return level?.tiles == null || own == null || own.frames.length === 0
      ? null
      : {
          tiles: level.tiles,
          own:
            own.tiles == null || own.k == null
              ? null
              : { tiles: own.tiles, k: own.k },
          nested:
            inner?.tiles == null || districts.length === 0
              ? null
              : { tiles: inner.tiles, ks: districts.map((row) => row.k) },
          frames: own.frames,
          blank: level.blank,
          isApproximate: level.isApproximate,
          noun: noun ?? level.noun,
          sources: [
            ...new Set(
              [...level.sources, ...own.sources].flatMap((source) =>
                source.attribution == null
                  ? [source.publisher]
                  : [source.publisher, source.attribution],
              ),
            ),
          ],
        };
  });
  const around = $derived.by(() => {
    const siblings = contest.scope.trail.at(-1)?.siblings;
    if (siblings == null) {
      return null;
    }
    return async () => {
      const found = await orpc.contests.places.call({
        id: siblings.contest,
        unit: siblings.unit ?? undefined,
        by: siblings.level,
      });
      const rows = (found?.rows ?? []).flatMap((row) =>
        row.path == null ? [] : [{ k: row.k, name: row.name, path: row.path }],
      );
      const own = drawn?.own?.k;
      const adjacent = new Set(
        own == null || rows.length === 0
          ? []
          : await orpc.contests.adjacent.call({
              id: siblings.contest,
              k: own,
              ks: rows.map((row) => row.k),
            }),
      );
      return rows.map((row) => ({ ...row, isAdjacent: adjacent.has(row.k) }));
    };
  });
  const locating = $derived.by(() => {
    const own = boundaries.find(
      (entry) => entry.k != null && entry.frames.length > 0,
    );
    const places = contest.locator ?? surround?.places ?? null;
    if (
      drawn != null ||
      chamber != null ||
      places == null ||
      own?.tiles == null ||
      own.k == null ||
      places.rows.every((row) => row.k === own.k)
    ) {
      return null;
    }
    const legend = places.legend;
    const parent = surround?.bounds.find(
      (entry) => entry.k != null && entry.frames.length > 0,
    );
    return {
      places,
      tiles: own.tiles,
      own: { tiles: own.tiles, k: own.k },
      frames:
        parent?.frames ??
        own.frames.map((frame) => {
          const wide = (frame.east - frame.west) * 1.5;
          const tall = (frame.north - frame.south) * 1.5;
          return {
            west: frame.west - wide,
            south: frame.south - tall,
            east: frame.east + wide,
            north: frame.north + tall,
          };
        }),
      isApproximate: own.isApproximate,
      noun: own.noun,
      sources: [
        ...new Set(
          own.sources.flatMap((source) =>
            source.attribution == null
              ? [source.publisher]
              : [source.publisher, source.attribution],
          ),
        ),
      ],
      colors: legend.map((item) => swatch(item.color, item.key)),
      names: legend.map((item) =>
        item.person == null ? (item.short ?? item.label) : item.label,
      ),
    };
  });
  const unmapped = $derived.by(() => {
    const total = contest.current?.valid ?? 0;
    let mapped = 0;
    for (const row of placed?.rows ?? []) {
      mapped += row.valid;
    }
    return total > 0 ? (total - mapped) / total : 0;
  });
  const titles = $derived(
    placed?.legend.map((item) =>
      item.person == null ? (item.short ?? item.label) : item.label,
    ) ?? [],
  );
  const tones = $derived(
    placed?.legend.map((item) => swatch(item.color, item.key)) ?? [],
  );
  const ladder = $derived.by(() => {
    const places = placed;
    const ranked = contest.electors?.rows ?? [];
    const rows = [...(places?.rows ?? []), ...(places?.districts ?? [])];
    const sides = ranked
      .slice(0, 2)
      .toSorted((a, b) => (a.lean ?? 1e9) - (b.lean ?? 1e9));
    const rungs = rows.flatMap((row) => {
      const item = places?.legend[row.leader ?? -1];
      const margin = row.margin ?? 0;
      const strength = [0.05, 0.1, 0.2].filter((edge) => margin >= edge).length;
      return row.seats == null || item == null
        ? []
        : [
            {
              k: row.k,
              name: row.name,
              path: row.path,
              seats: row.seats,
              side: sides.findIndex((side) => side.person === item.person),
              leader: titles[row.leader ?? -1] ?? "",
              margin,
              color: `color-mix(in oklab, ${tones[row.leader ?? -1]} ${[34, 54, 76, 100][strength]}%, var(--map-low))`,
            },
          ];
    });
    let isMatched = sides.length === 2 && rungs.length === rows.length;
    for (const [index, side] of sides.entries()) {
      let won = 0;
      for (const rung of rungs) {
        if (rung.side === index) {
          won += rung.seats;
        }
      }
      isMatched &&= side.person != null && won === side.votes;
    }
    return !isMatched
      ? null
      : [
          ...rungs
            .filter((rung) => rung.side === 0)
            .toSorted((a, b) => b.margin - a.margin),
          ...rungs.filter((rung) => rung.side === -1),
          ...rungs
            .filter((rung) => rung.side === 1)
            .toSorted((a, b) => a.margin - b.margin),
        ];
  });
  const levels = $derived(
    placed?.levels.map((level) => {
      const query = new URLSearchParams({
        ...Object.fromEntries(page.url.searchParams),
        by: level.code,
      })
        .toString()
        .replaceAll("%2F", "/");
      return {
        code: level.code,
        label: level.noun.charAt(0).toUpperCase() + level.noun.slice(1),
        count: level.count,
        path: here,
        query,
        isCurrent: level.code === placed.level,
      };
    }) ?? [],
  );
  const keyed = $derived.by(() => {
    const rows = (placed?.rows ?? []).filter(
      (row) => row.leader != null && row.valid > 0,
    );
    if (rows.length < 6) {
      return [];
    }
    const median =
      rows.toSorted((a, b) => a.valid - b.valid)[Math.floor(rows.length / 2)]
        ?.valid ?? 0;
    const sizable = rows.filter((row) => row.valid >= median / 10);
    const largest = {
      label: m.key_largest(),
      rows: rows.toSorted((a, b) => b.valid - a.valid).slice(0, 3),
      value: (row: (typeof rows)[number]) => count(row.valid),
    };
    if (titles.length < 2) {
      return [largest];
    }
    return [
      {
        label: m.key_closest(),
        rows: sizable
          .toSorted((a, b) => (a.margin ?? 0) - (b.margin ?? 0))
          .slice(0, 3),
        value: (row: (typeof rows)[number]) => points(row.margin ?? 0),
      },
      largest,
      ...[0, 1]
        .filter((index) => titles[index] != null)
        .map((index) => ({
          label: m.key_strongest({ name: titles[index] ?? "" }),
          rows: sizable
            .toSorted((a, b) => (b.shares[index] ?? 0) - (a.shares[index] ?? 0))
            .slice(0, 1),
          value: (row: (typeof rows)[number]) =>
            percent(row.shares[index] ?? 0),
        })),
    ];
  });
  const only = $derived(filter.at === page.url.href ? filter.value : null);
  const sets = $derived(
    contest.sets.filter(
      (set, index) =>
        contest.sets.findIndex(
          (other) =>
            other.stage === set.stage &&
            other.status === set.status &&
            other.isDefault === set.isDefault &&
            other.source.publisher === set.source.publisher &&
            other.source.title === set.source.title,
        ) === index,
    ),
  );
  const drilled = $derived(contest.scope.noun != null);
  const counted = $derived(
    contest.count?.stv != null && contest.count.round === 1
      ? m.stv_first()
      : MEASURES[contest.current?.measure ?? ""]?.(),
  );
  const question = $derived(
    contest.rows.some((row) => row.key.startsWith("answer:")),
  );
  const seated = $derived.by(() => {
    let total = 0;
    for (const row of contest.rows) {
      total += row.current?.seats ?? 0;
    }
    return total > 1;
  });
  const phrases = $derived.by(() => {
    const mine = contest.name.replace(/^\d{4}\s+/u, "").split(" ");
    return new Map(
      contest.targets.map((target) => {
        const theirs = target.label.replace(/^\d{4}\s+/u, "").split(" ");
        const shared = Math.min(mine.length, theirs.length);
        let start = 0;
        while (start < shared && mine[start] === theirs[start]) {
          start += 1;
        }
        let end = 0;
        while (
          end < shared - start &&
          mine.at(-1 - end) === theirs.at(-1 - end)
        ) {
          end += 1;
        }
        const phrase = theirs.slice(start, theirs.length - end).join(" ");
        return [target.value, { phrase, whole: theirs.join(" ") }] as const;
      }),
    );
  });
  const span = $derived.by(() => {
    const target = contest.target;
    const own = contest.election;
    if (
      target?.date == null ||
      target.year == null ||
      own.date == null ||
      own.year == null
    ) {
      return null;
    }
    const race = contest.comparison.notes.some(
      (note) => note.code === "different_race",
    )
      ? (phrases.get(target.path ?? "")?.phrase ?? "")
      : "";
    const label = (date: string, year: number) =>
      target.year === own.year
        ? new Intl.DateTimeFormat(getLocale(), {
            month: "short",
            year: "numeric",
            timeZone: "UTC",
          }).format(new Date(`${date}T00:00:00Z`))
        : String(year);
    const mine = label(own.date, own.year);
    const theirs = label(target.date, target.year);
    const forward = target.date > own.date;
    return {
      from: forward ? mine : theirs,
      to: forward ? theirs : mine,
      forward,
      race: race === "" ? null : race,
    };
  });
  const change = $derived.by(() => {
    if (span == null || contest.chamber == null) {
      return null;
    }
    const moves = contest.rows.flatMap((row) => {
      const before = span.forward ? row.current : row.earlier;
      const after = span.forward ? row.earlier : row.current;
      return before?.seats == null
        ? []
        : [[row.key, (after?.seats ?? 0) - before.seats] as const];
    });
    return moves.length === 0 ? null : Object.fromEntries(moves);
  });
  const members = $derived.by(() => {
    const won = contest.rows.filter((row) => (row.current?.seats ?? 0) > 0);
    return won.length > 1 &&
      won.every((row) => row.current?.person != null && row.current.seats === 1)
      ? won
      : null;
  });
  const lead = $derived.by(() => {
    if (members != null) {
      const last = members
        .map((row) => row.current?.share ?? 0)
        .toSorted((a, b) => a - b)[0];
      const loser = contest.rows
        .filter(
          (row) =>
            row.current != null &&
            !row.key.startsWith("kind:") &&
            !members.includes(row),
        )
        .toSorted(
          (a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0),
        )[0];
      const row = members.find((member) => member.current?.share === last);
      return contest.isStv || row == null || loser?.current == null
        ? null
        : { row, margin: (last ?? 0) - loser.current.share };
    }
    const [top, next] = contest.rows
      .filter((row) => row.current != null && !row.key.startsWith("kind:"))
      .toSorted((a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0));
    return top == null || next == null
      ? null
      : {
          row: top,
          margin: (top.current?.share ?? 0) - (next.current?.share ?? 0),
        };
  });
  const footnotes = $derived(
    Map.groupBy(
      [
        ...contest.rows.flatMap((row) =>
          row.current?.outcome == null ||
          row.current.note == null ||
          isDerived(row.current.note)
            ? []
            : [{ label: row.label, note: row.current.note }],
        ),
        ...(contest.current == null ? (contest.declared?.rows ?? []) : [])
          .filter((row) => row.note != null && !isDerived(row.note))
          .map((row) => ({ label: row.label, note: row.note ?? "" })),
      ],
      (row) => row.note,
    )
      .entries()
      .map(([note, rows]) =>
        question
          ? note
          : m.outcome_note({
              names: new Intl.ListFormat(getLocale(), {
                type: "conjunction",
              }).format(rows.map((row) => row.label)),
              note,
            }),
      )
      .toArray()
      .concat(
        contest.partial == null
          ? []
          : [m.race_partial_counties(contest.partial)],
      ),
  );
  const verdict = $derived.by(() => {
    if (contest.current == null) {
      return null;
    }
    const named = (row: {
      label: string;
      short: string | null;
      person?: string | null;
      current?: { person: string | null } | null;
    }) =>
      row.label.length > 28 && (row.person ?? row.current?.person) == null
        ? (row.short ?? row.label)
        : row.label;
    if (question) {
      const top = contest.rows
        .filter((row) => row.current != null)
        .toSorted(
          (a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0),
        )[0];
      const next = contest.rows
        .filter((row) => row.current != null && row !== top)
        .toSorted(
          (a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0),
        )[0];
      const yes = /^(?:yes|for|approve|(?:sì|si|oui|ja|evet|sim)(?!\p{L}))/iu;
      const turned =
        contest.turnout == null || contest.turnout.registered === 0
          ? null
          : contest.turnout.voters / contest.turnout.registered;
      return top?.current?.outcome === "lost" &&
        yes.test(top.label) &&
        contest.quorum != null &&
        turned != null &&
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
              ? m.race_lede_measure_passed({
                  share: percent(top.current.share),
                })
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
        for (const row of contest.electors?.rows ?? []) {
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
    if (seated) {
      let total = 0;
      for (const row of contest.rows) {
        total += row.current?.seats ?? 0;
      }
      if (members != null && (members.length <= 4 || contest.isStv)) {
        const names = new Intl.ListFormat(getLocale(), {
          style: "long",
          type: "conjunction",
        }).format(
          members.map((row) =>
            row.short == null
              ? row.label
              : m.race_member({ name: row.label, party: row.short }),
          ),
        );
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
          contest.partners.find((pair) => pair.includes(row.key))?.[0] ??
          row.key,
      )
        .values()
        .map((members) => {
          let seats = 0;
          let share = 0;
          for (const row of members) {
            seats += row.current?.seats ?? 0;
            share += row.current?.share ?? 0;
          }
          return { names: members.map(named), seats, share };
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
                (((first.votes[last] ?? 0) - (second.votes[last] ?? 0)) /
                  total) *
                  1000,
              ) / 10,
            ),
            runner: definite(named(second)),
          });
    }
    const [top, next] = contest.rows
      .filter((row) => row.current != null && !row.key.startsWith("kind:"))
      .toSorted((a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0));
    if (top?.current != null && next == null) {
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
      margin: points(top.current.share - next.current.share).replace(
        /^\+/u,
        "",
      ),
      runner: named(next),
    });
    return isWon && withdrew != null && withdrew.key !== next.key
      ? `${lede} ${m.race_lede_withdrew({ name: named(withdrew) })}`
      : lede;
  });
  const held = $derived.by(() => {
    if (handover == null || contest.current == null) {
      return null;
    }
    if (handover.seats != null) {
      const label = (party: { label: string; short: string | null } | null) =>
        party?.short ?? party?.label ?? m.seats_independents();
      return `${m.change_seats({
        changes: new Intl.ListFormat(getLocale(), {
          style: "long",
          type: "conjunction",
        }).format(
          handover.seats.map((seat) =>
            seat.isGain
              ? seat.count > 1
                ? m.change_seats_gain({
                    to: label(seat.party),
                    from: label(seat.from),
                    count: count(seat.count),
                  })
                : m.gains_seat({
                    to: label(seat.party),
                    from: label(seat.from),
                  })
              : seat.count > 1
                ? m.change_seats_hold({
                    party: label(seat.party),
                    count: count(seat.count),
                  })
                : m.byes_hold({ party: label(seat.party) }),
          ),
        ),
      })}${handover.basis === "same" ? "" : "*"}`;
    }
    const [top, next] = contest.rows
      .filter((row) => row.current != null && !row.key.startsWith("kind:"))
      .toSorted((a, b) => (b.current?.votes ?? 0) - (a.current?.votes ?? 0));
    const label =
      handover.party?.short ?? handover.party?.label ?? m.seats_independents();
    const party =
      handover.alliance == null
        ? label
        : m.change_allied({ alliance: handover.alliance, party: label });
    const from =
      handover.from?.short ?? handover.from?.label ?? m.seats_independents();
    const majority =
      top?.current != null && next?.current != null && top.current.votes > 0
        ? count(top.current.votes - next.current.votes)
        : null;
    const [gain, hold] =
      handover.lead === "margin"
        ? [m.change_gain_margin, m.change_hold_margin]
        : [m.change_gain, m.change_hold];
    return `${
      majority == null
        ? handover.isGain
          ? m.change_gain_bare({ party, from })
          : m.change_hold_bare({ party })
        : handover.isGain
          ? gain({ party, from, majority })
          : hold({ party, majority })
    }${handover.basis === "same" ? "" : "*"}`;
  });
  const comeback = $derived.by(() => {
    if (question || contest.current == null) {
      return null;
    }
    const winner = contest.rows.find((row) =>
      ["won", "elected"].includes(row.current?.outcome ?? ""),
    );
    const same = (key: string, person: string | null) =>
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
      return leader == null || total === 0 || same(leader.key, leader.person)
        ? null
        : m.race_lede_first_led({
            name: leader.label,
            share: percent((leader.votes[0] ?? 0) / total),
          });
    }
    const index = rounds.findIndex((round) => round.isCurrent);
    const leader = rounds[index - 1]?.leaders[0];
    return rounds[index]?.kind !== "runoff" ||
      leader == null ||
      same(leader.key, leader.person)
      ? null
      : m.race_lede_first_led({
          name: leader.label,
          share: percent(leader.share),
        });
  });
  const items = $derived.by(() => {
    const winners = new Map(
      contest.series.map((entry) => [entry.date, entry.winner]),
    );
    const day = new Intl.DateTimeFormat(getLocale(), {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
    const dated = new Intl.DateTimeFormat(getLocale(), {
      dateStyle: "medium",
      timeZone: "UTC",
    });
    return [
      {
        value: "none",
        label: m.compare_none(),
        year: null,
        phrase: "",
        when: "",
        winner: null,
        annulled: false,
      },
      ...contest.targets
        .toSorted((a, b) => Number(b.series) - Number(a.series))
        .map((target) => {
          const winner = target.series
            ? (winners.get(target.detail) ?? null)
            : null;
          const words = phrases.get(target.value);
          return {
            value: target.value,
            label: target.label,
            year: target.year?.toString() ?? null,
            phrase:
              target.series || words == null || words.phrase === ""
                ? (words?.whole ?? target.label)
                : words.phrase,
            when:
              target.detail == null
                ? ""
                : (target.detail.startsWith(`${target.year}`)
                    ? day
                    : dated
                  ).format(new Date(`${target.detail}T00:00:00Z`)),
            winner,
            annulled: target.annulled,
            group: target.series ? m.compare_series() : m.compare_other(),
            keywords: [target.detail ?? "", winner?.label ?? ""],
          };
        }),
    ];
  });
  const matches = $derived(
    [
      {
        value: "party",
        label: m.match_party(),
        detail: m.match_party_detail(),
      },
      {
        value: "person",
        label: m.match_person(),
        detail: m.match_person_detail(),
      },
      {
        value: "family",
        label: m.match_family(),
        detail: m.match_family_detail(),
      },
    ].map((option) => ({
      ...option,
      query: new URLSearchParams({
        ...Object.fromEntries(page.url.searchParams),
        match: option.value,
      })
        .toString()
        .replaceAll("%2F", "/"),
    })),
  );
  const grouping = $derived(
    page.url.searchParams.get("group") === "alliance" ? "alliance" : "option",
  );
  const groupings = $derived(
    [
      {
        value: "option",
        label: m.group_option(),
        detail: m.group_option_detail(),
      },
      {
        value: "alliance",
        label: m.group_alliance(),
        detail: m.group_alliance_detail(),
      },
    ].map((option) => {
      const text = new URLSearchParams([
        ...[...page.url.searchParams].filter(([key]) => key !== "group"),
        ...(option.value === "alliance" ? [["group", "alliance"]] : []),
      ])
        .toString()
        .replaceAll("%2F", "/");
      return {
        ...option,
        suffix: text === "" ? ("" as const) : (`?${text}` as const),
      };
    }),
  );
  const below = $derived.by(() => {
    const places = placed;
    const level = places?.levels.find((entry) => entry.code === places.level);
    return places == null ||
      level == null ||
      places.rows.every((row) => row.path == null)
      ? null
      : { places, noun: level.noun };
  });
  const previous = $derived(
    contest.targets.find((target) => target.previous) ?? null,
  );
  const isPrevious = $derived(
    previous != null && contest.target?.path === previous.value,
  );
  const toggled = $derived.by(() => {
    const text = new URLSearchParams([
      ...[...page.url.searchParams].filter(([key]) => key !== "vs"),
      ...(isPrevious ? [] : [["vs", "previous"]]),
    ])
      .toString()
      .replaceAll("%2F", "/");
    return text === "" ? ("" as const) : (`?${text}` as const);
  });
  const publishers = $derived([
    ...new Set(contest.current?.sets.map((set) => set.source.publisher) ?? []),
  ]);
  const tier = (set: (typeof contest.sets)[number]) =>
    set.unreconciled != null
      ? "unreconciled"
      : set.source.kind === "community"
        ? "secondary"
        : set.stage;
  const secondary = $derived(
    contest.current?.sets.some((set) => tier(set) === "secondary") ?? false,
  );
  const unreconciled = $derived(
    (placed?.unreconciled ?? []).map((flag) =>
      flag.reason === "no official total" || flag.official == null
        ? {
            lead: null,
            body: m.places_unreconciled_missing({ publisher: flag.publisher }),
          }
        : {
            lead: m.places_unreconciled_lead({ publisher: flag.publisher }),
            body: [
              ...(flag.reason === "overcounted" || flag.drifted.length === 0
                ? [
                    m.places_unreconciled_overcounted({
                      counted: count(flag.counted),
                      official: count(flag.official),
                    }),
                  ]
                : flag.drifted.map((drift) =>
                    m.places_unreconciled_drifted({
                      party: drift.party,
                      counted: count(drift.counted),
                      official: count(drift.official),
                    }),
                  )),
              m.places_unreconciled_official(),
            ].join(" "),
          },
    ),
  );
  const captions = $derived([
    ...new Set(
      contest.current?.sets.map((set) =>
        set.source.kind !== "official_body" &&
        ["live", "preliminary", "official", "certified", "recount"].includes(
          tier(set),
        )
          ? m.sources_caption_compiled({
              stage: STAGES[tier(set)]?.() ?? set.stage,
              publisher: set.source.publisher,
            })
          : m.sources_caption({
              stage: STAGES[tier(set)]?.() ?? set.stage,
              publisher: set.source.publisher,
            }),
      ) ?? [],
    ),
  ]);
  const notes = $derived([
    ...contest.comparison.notes
      .filter(
        (note) =>
          span == null ||
          (note.code !== "different_race" && note.code !== "redrawn"),
      )
      .map((note) => {
        if (note.code === "partial") {
          const value = percent(Number(note.value));
          return note.side === "target"
            ? m.note_partial_target({ percent: value })
            : m.note_partial_current({ percent: value });
        }
        if (note.code === "redrawn") {
          return m.note_redrawn({
            from: span?.from ?? "",
            to: span?.to ?? "",
            percent: percent(Number(note.value) / 100),
          });
        }
        if (note.code === "alternative") {
          const publisher = note.value ?? "";
          return note.side === "target"
            ? m.note_alternative_target({ publisher })
            : m.note_alternative_current({ publisher });
        }
        return {
          no_target: m.note_no_target,
          not_covered: m.note_not_covered,
          different_race: m.note_different_race,
          annulled: m.note_annulled,
        }[note.code]();
      }),
    ...contest.notes.map((note) => note.body),
  ]);
  const checks = $derived(
    contest.checks.map((check) => ({
      code: check.code,
      summary:
        check.code === "accounting.turnout"
          ? m.checks_turnout({ count: check.units.length })
          : m.checks_votes_valid({ count: check.units.length }),
      units: check.units.map((unit) =>
        check.code === "accounting.turnout"
          ? m.check_turnout_unit({
              name: unit.name,
              ballots: count(unit.details.ballots_cast ?? 0),
              registered: count(unit.details.registered ?? 0),
            })
          : m.check_votes_valid_unit({
              name: unit.name,
              votes: count(unit.details.votes ?? 0),
              valid: count(unit.details.valid ?? 0),
            }),
      ),
    })),
  );
</script>

<header
  class="col-span-12 grid gap-x-8 gap-y-3 bg-card px-3.5 pbs-4 pbe-3.5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end"
>
  <div class="grid gap-1.5 min-inline-0">
    {#if drilled && contest.path != null}
      <Preview
        class="justify-self-start text-[0.9375rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
        path={contest.path}>{contest.name}</Preview
      >
    {/if}
    <h1 class="title" data-scope-title>
      {drilled ? contest.scope.name : contest.name}
    </h1>
    <p
      class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem] text-muted-foreground"
    >
      {#if drilled}
        <span class="first-letter:uppercase"
          >{m.scope_in({
            noun: contest.scope.noun ?? "",
            parent: contest.scope.parent ?? "",
          })}</span
        >
      {:else if contest.election.path == null}
        <span>{contest.election.name}</span>
      {:else}
        <Preview
          class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          path={contest.election.path}>{contest.election.name}</Preview
        >
      {/if}
      <time datetime={contest.election.date}>{date(contest.election.date)}</time
      >
      {#if contest.scope.home != null}
        <Preview
          class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          path={contest.scope.home}
          >{m.scope_home({ place: contest.scope.name })}</Preview
        >
      {/if}
      {#if contest.election.status === "annulled"}
        <span class="highlighter px-1 font-semibold text-foreground"
          >{m.election_annulled()}</span
        >
      {/if}
    </p>
    {#if verdict != null}
      <p
        class="mbs-1 font-serif text-[clamp(1.0625rem,1.5vw,1.25rem)]/snug text-pretty max-inline-[60ch]"
      >
        {#if held != null}{held}{" "}{/if}{verdict}{#if comeback != null}{" "}{comeback}{/if}
      </p>
    {/if}
    {#if held != null && handover != null && handover.basis !== "same"}
      <p
        class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[60ch]"
      >
        * {(handover.basis === "area"
          ? m.change_note_area
          : m.change_note_name)({ year: String(handover.since) })}
        {#if handover.prior.path != null}
          <a
            class="underline decoration-border underline-offset-4 hover:decoration-foreground"
            href={resolve("/[...path]", { path: handover.prior.path })}
            >{handover.prior.name}</a
          >{:else}{handover.prior.name}{/if}.
      </p>
    {/if}
    {#each footnotes as line (line)}
      <p
        class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[60ch]"
      >
        {line}
      </p>
    {/each}
    {#if contest.wording.length > 0}
      <Wording wording={contest.wording} />
    {/if}
  </div>
  {#if contest.series.length > 1}
    <Series series={contest.series} />
  {/if}
  {#if origins.length > 0}
    <p
      class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[60ch] md:col-span-2"
    >
      {#each origins as part, index (index)}{#if part.path == null}{part.text}{:else}<Preview
            class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
            path={part.path}>{part.text}</Preview
          >{/if}{/each}
    </p>
  {/if}
  {#if heirs.length > 0}
    <p
      class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[60ch] md:col-span-2"
    >
      {#each heirs as part, index (index)}{#if part.path == null}{part.text}{:else}<Preview
            class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
            path={part.path}>{part.text}</Preview
          >{/if}{/each}
    </p>
  {/if}
  {#if contest.county != null}
    {const parts = $derived(
      m.contest_county({ county: "\u0001" }).split("\u0001"),
    )}
    <p
      class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[60ch] md:col-span-2"
    >
      {parts[0]}{#if contest.county.path == null}{contest.county
          .name}{:else}<Preview
          class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          path={contest.county.path}>{contest.county.name}</Preview
        >{/if}{parts[1]}
    </p>
  {/if}
  {#if contest.scope.trail.length > 1 || below != null}
    <nav class="md:col-span-2" aria-label={m.scope_path()}>
      <ol class="flex flex-wrap items-center gap-y-1 text-sm">
        {#each contest.scope.trail as crumb, index (index)}
          <li class="flex items-center">
            {#if index > 0}
              <span class="px-1.5 text-muted-foreground" aria-hidden="true"
                >/</span
              >
            {/if}
            {#if index < contest.scope.trail.length - 1 && crumb.path != null}
              <Preview class="hover:highlighter" path={crumb.path}
                >{crumb.name}</Preview
              >
            {:else}
              <span class="font-semibold" aria-current="page">{crumb.name}</span
              >
            {/if}
            {#if crumb.siblings != null}
              <PlaceSwitcher
                source={crumb.siblings}
                noun={crumb.siblings.noun}
                current={crumb.name}
              />
            {/if}
          </li>
        {/each}
        {#if below != null}
          <li class="flex items-center">
            <span class="px-1.5 text-muted-foreground" aria-hidden="true"
              >/</span
            >
            <PlaceSwitcher
              source={below.places}
              noun={below.noun}
              current={null}
            />
          </li>
        {/if}
      </ol>
    </nav>
  {/if}
</header>

{#if rounds.length > 1}
  <Rounds {rounds} />
{/if}

{#if contest.current == null && contest.declared != null}
  <section class="col-span-12 grid gap-2.5 bg-card px-3.5 py-4">
    <p class="text-[0.9375rem] text-muted-foreground">
      {DECLARED[contest.declared.kind]?.() ?? ""}
    </p>
    <ul class="grid gap-1.5 text-sm/tight">
      {#each contest.declared.rows as row (row.key)}
        <li
          class="flex items-baseline gap-2"
          style:--party={swatch(row.color, row.key)}
        >
          <span
            class="shrink-0 self-center rounded-xs bg-(--party) chip block-2.5 inline-2.5"
          ></span>
          <span class="min-inline-0">
            <Name
              class="font-semibold"
              label={row.label}
              color={swatch(row.color, row.key)}
              hasSwatch={false}
              person={row.person}
              party={row.party}
            />
            {#if row.short != null && row.short !== row.label}
              <Name
                class="text-[0.8125rem] text-muted-foreground"
                label={row.short}
                color={swatch(row.color, row.key)}
                hasSwatch={false}
                party={row.party}
              />
            {/if}
            {#if row.note == null}
              <span
                class="ms-1 highlighter px-1 text-xs font-semibold text-foreground"
                >{OUTCOMES[row.outcome]?.() ?? row.outcome}</span
              >
            {:else}
              <Term
                class="ms-1 highlighter px-1 text-xs font-semibold text-foreground"
                label={OUTCOMES[row.outcome]?.() ?? row.outcome}
                definition={explain(row.note)}
              />
            {/if}
          </span>
        </li>
      {/each}
    </ul>
  </section>
{:else if contest.current == null && contest.turnout != null}
  {@render stats(false)}
  <p class="col-span-12 bg-card px-3.5 py-6 text-muted-foreground">
    {m.no_counts()}
  </p>
{:else if contest.current == null}
  <p class="col-span-12 bg-card px-3.5 py-6 text-muted-foreground">
    {m.no_results()}
  </p>
{:else}
  {#if contest.count != null}
    <Count count={contest.count} {here} isDerived={contest.isDerived} />
  {/if}
  {#if contest.electors != null}
    <Electors electors={contest.electors} {ladder} bind:focus />
  {/if}
  {#if drawn != null}
    {@render caveat()}
  {/if}
  {#if drawn != null && placed != null}
    <div
      class={cn("col-span-12 bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0", {
        "lg:col-span-8": chamber != null,
        "lg:col-span-7 xl:col-span-8": chamber == null,
      })}
    >
      {#key `${here}:${drawn.tiles}`}
        <PlaceMap
          places={placed}
          colors={tones}
          shade={titles.length === 1 ? "bubbles" : "winner"}
          tiles={drawn.tiles}
          own={drawn.own}
          nested={drawn.nested}
          frames={drawn.frames}
          blank={drawn.blank}
          isApproximate={drawn.isApproximate}
          noun={drawn.noun}
          sources={drawn.sources}
          explore={here}
          neighbors={around}
          up={upward}
          votes={counted ?? m.col_votes()}
          names={titles}
          {levels}
          {span}
          isLinked
          bind:only={
            () => only,
            (value: number | string | null) =>
              (filter = { at: page.url.href, value })
          }
          bind:focus
        />
      {/key}
      {#if grouped != null && placed.rows.some( (row) => placed.legend[row.leader ?? -1]?.key.startsWith("kind:others") )}
        <p class="px-1 pbs-1 text-xs text-pretty text-muted-foreground">
          {m.places_others_note({ name: grouped.label })}
        </p>
      {/if}
      {#if unmapped >= 0.01}
        <p class="px-1 pbs-1 text-xs text-pretty text-muted-foreground">
          {m.map_unmapped({ percent: percent(unmapped) })}
        </p>
      {/if}
    </div>
    {#if chamber == null}
      <div
        class="col-span-12 grid grid-rows-[auto_1fr] gap-px bg-border min-inline-0 lg:col-span-5 xl:col-span-4"
      >
        {@render stats(true)}
        <div
          class="@container grid content-start gap-1 bg-card px-1.5 pbs-2 pbe-2.5"
        >
          {@render results()}
        </div>
      </div>
    {:else}
      <Seats
        isLinked
        parties={chamber.parties}
        places={chamber.places}
        here={chamber.here}
        place={chamber.places[chamber.here ?? -1]?.name ?? contest.scope.name}
        link={chamber.link}
        size={chamber.size}
        {change}
      />
    {/if}
    {#if keyed.length > 0}
      <section
        class="col-span-12 flex flex-wrap items-center gap-x-5 gap-y-2.5 bg-card px-3.5 py-3"
        aria-labelledby="key-places"
      >
        <h2 id="key-places" class="text-sm font-semibold">{m.key_places()}</h2>
        {#each keyed as group (group.label)}
          <div class="flex flex-wrap items-center gap-1.5">
            <span class="text-[0.8125rem] text-muted-foreground"
              >{group.label}</span
            >
            {#each group.rows as row (row.k)}
              <svelte:element
                this={row.path == null ? "span" : "a"}
                class={cn(
                  "flex items-center gap-1.5 rounded-xs px-1.5 text-[0.8125rem] block-7 hover:bg-secondary",
                  { "bg-secondary": focus === row.k },
                )}
                href={row.path == null
                  ? undefined
                  : resolve("/[...path]", { path: row.path })}
                style:--party={tones[row.leader ?? 0]}
                role={row.path == null ? "presentation" : undefined}
                onpointerenter={() => (focus = row.k)}
                onpointerleave={() => (focus = null)}
                onfocus={() => (focus = row.k)}
                onblur={() => (focus = null)}
              >
                <span class="rounded-xs bg-(--party) chip block-2.5 inline-2.5"
                ></span>
                <span class="font-semibold">{row.name}</span>
                <span
                  class="font-figures text-xs text-muted-foreground tabular-nums"
                  >{group.value(row)}</span
                >
              </svelte:element>
            {/each}
          </div>
        {/each}
      </section>
    {/if}
  {:else if locating != null}
    <div
      class="col-span-12 bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0 lg:col-span-7 xl:col-span-8"
    >
      <PlaceMap
        places={locating.places}
        colors={locating.colors}
        tiles={locating.tiles}
        own={locating.own}
        frames={locating.frames}
        isApproximate={locating.isApproximate}
        noun={locating.noun}
        sources={locating.sources}
        explore={upward?.path ?? null}
        up={upward}
        votes={m.col_votes()}
        names={locating.names}
        levels={[]}
        span={null}
        isLinked
      />
    </div>
    <div
      class="col-span-12 grid grid-rows-[auto_1fr] gap-px bg-border min-inline-0 lg:col-span-5 xl:col-span-4"
    >
      {@render stats(true)}
      <div
        class="@container grid content-start gap-1 bg-card px-1.5 pbs-2 pbe-2.5"
      >
        {@render results()}
      </div>
    </div>
  {:else}
    {@render stats(false)}
  {/if}
  {#if additional != null}
    <Additional {additional} />
  {/if}
  {#if contest.count?.stv != null}
    <Stv count={contest.count} stv={contest.count.stv} {here} />
  {:else if contest.count != null}
    <Tally count={contest.count} {here} />
  {/if}

  <div
    class="col-span-12 flex flex-wrap items-center gap-x-6 gap-y-3 bg-card px-3.5 py-3"
  >
    <div
      class="flex flex-1 basis-80 flex-wrap items-center gap-x-2.5 gap-y-1.5 min-inline-0"
    >
      <span class="shrink-0 text-sm font-semibold max-sm:basis-full"
        >{m.compare_label()}</span
      >
      <Combobox
        class="flex-1 max-inline-md"
        wide
        {items}
        value={contest.target?.path ?? null}
        label={m.compare_label()}
        placeholder={m.compare_placeholder()}
        search={m.compare_search()}
        empty={m.compare_empty()}
        onValueChange={(value: string | null) => {
          const query = new URLSearchParams(page.url.search);
          if (value == null || value === "none") {
            query.delete("vs");
          } else {
            query.set("vs", value);
          }
          const text = query.toString().replaceAll("%2F", "/");
          const suffix = text === "" ? ("" as const) : (`?${text}` as const);
          void goto(resolve(`/[...path]${suffix}`, { path: here }), {
            reset: false,
          });
        }}
      >
        {#snippet row(item: (typeof items)[number])}
          {#if item.year == null}
            <span class="flex-1 min-inline-0">{item.label}</span>
          {:else}
            <ElectionRow
              year={item.year}
              winner={item.winner}
              phrase={item.phrase}
              when={item.when}
              annulled={item.annulled}
            />
          {/if}
        {/snippet}
        {#snippet selection(item: (typeof items)[number])}
          {#if item.year == null}
            <span class="flex-1 truncate min-inline-0">{item.label}</span>
          {:else}
            <span
              class="flex flex-1 items-baseline gap-2 min-inline-0"
              title={item.label}
            >
              <span class="shrink-0 font-figures font-semibold tabular-nums"
                >{item.year}</span
              >
              {#if item.winner == null}
                <span class="truncate">{item.phrase}</span>
              {:else}
                <span
                  class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
                  style:background-color={swatch(
                    item.winner.color,
                    item.winner.key,
                  )}
                ></span>
                <span class="truncate">{item.winner.label}</span>
              {/if}
            </span>
          {/if}
        {/snippet}
      </Combobox>
      {#if previous != null}
        <Tooltip.Root disableCloseOnTriggerClick delayDuration={300}>
          <Tooltip.Trigger>
            {#snippet child({ props })}
              <a
                {...props}
                type={undefined}
                class={cn(
                  "flex shrink-0 items-center gap-1.5 rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary",
                  {
                    "bg-primary text-primary-foreground hover:bg-primary":
                      isPrevious,
                  },
                )}
                href={resolve(`/[...path]${toggled}`, { path: here })}
                aria-current={isPrevious ? "true" : undefined}
                data-sveltekit-reset="false"
                >{m.compare_previous()}
                {#if previous.year != null}
                  <span class="font-figures tabular-nums">{previous.year}</span>
                {/if}</a
              >
            {/snippet}
          </Tooltip.Trigger>
          <Tooltip.Content
            collisionPadding={8}
            class="leading-snug max-inline-64"
          >
            {isPrevious ? m.compare_previous_off() : m.compare_previous_on()}
          </Tooltip.Content>
        </Tooltip.Root>
      {/if}
      {#if contest.target != null}
        <Tooltip.Root disableCloseOnTriggerClick>
          <Tooltip.Trigger
            class="shrink-0 rounded-xs highlighter px-1 text-[0.8125rem] font-semibold"
          >
            {contest.comparison.label === "exact"
              ? m.quality_exact()
              : m.quality_estimated()}
          </Tooltip.Trigger>
          <Tooltip.Content
            collisionPadding={8}
            class="leading-snug max-inline-64"
          >
            {contest.comparison.label === "exact"
              ? m.quality_exact_detail()
              : m.quality_estimated_detail()}
          </Tooltip.Content>
        </Tooltip.Root>
      {/if}
    </div>
    {#if !question}
      <div
        class="flex items-center gap-2.5"
        role="group"
        aria-labelledby="match-label"
      >
        <span id="match-label" class="text-sm font-semibold"
          >{m.match_label()}</span
        >
        <div class="flex">
          <Tooltip.Root disableCloseOnTriggerClick {tether} delayDuration={300}>
            {#snippet children({ payload })}
              {#each matches as option (option.value)}
                <Tooltip.Trigger {tether} payload={option.detail}>
                  {#snippet child({ props })}
                    <a
                      {...props}
                      type={undefined}
                      class={cn(
                        "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                        {
                          "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                            option.value === match,
                        },
                      )}
                      href={resolve(`/[...path]?${option.query}`, {
                        path: here,
                      })}
                      aria-current={option.value === match ? "true" : undefined}
                      data-sveltekit-reset="false">{option.label}</a
                    >
                  {/snippet}
                </Tooltip.Trigger>
              {/each}
              <Tooltip.Content
                collisionPadding={8}
                class="leading-snug max-inline-64">{payload}</Tooltip.Content
              >
            {/snippet}
          </Tooltip.Root>
        </div>
      </div>
    {/if}
    {#if contest.current.isAllied || grouping === "alliance"}
      <div
        class="flex items-center gap-2.5"
        role="group"
        aria-labelledby="group-label"
      >
        <span id="group-label" class="text-sm font-semibold"
          >{m.group_label()}</span
        >
        <div class="flex">
          <Tooltip.Root disableCloseOnTriggerClick {tether} delayDuration={300}>
            {#snippet children({ payload })}
              {#each groupings as option (option.value)}
                <Tooltip.Trigger {tether} payload={option.detail}>
                  {#snippet child({ props })}
                    <a
                      {...props}
                      type={undefined}
                      class={cn(
                        "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                        {
                          "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                            option.value === grouping,
                        },
                      )}
                      href={resolve(`/[...path]${option.suffix}`, {
                        path: here,
                      })}
                      aria-current={option.value === grouping
                        ? "true"
                        : undefined}
                      data-sveltekit-reset="false">{option.label}</a
                    >
                  {/snippet}
                </Tooltip.Trigger>
              {/each}
              <Tooltip.Content
                collisionPadding={8}
                class="leading-snug max-inline-64">{payload}</Tooltip.Content
              >
            {/snippet}
          </Tooltip.Root>
        </div>
      </div>
    {/if}
  </div>

  {#if span != null && contest.target != null}
    <Changes
      rows={contest.rows}
      {span}
      valid={span.forward
        ? { before: contest.current.valid, after: contest.target.valid }
        : { before: contest.target.valid, after: contest.current.valid }}
      counted={counted ?? m.stat_valid()}
      redrawn={contest.comparison.notes.find((note) => note.code === "redrawn")
        ?.value ?? null}
      places={placed}
      picked={only}
      onpick={(value: string | null) => {
        filter = { at: page.url.href, value };
        if (value != null) {
          document.querySelector("[data-places]")?.scrollIntoView();
        }
      }}
    />
    {#if placed != null}
      <Shift
        places={placed}
        {span}
        colors={tones}
        names={titles}
        noun={placed.levels.find((level) => level.code === placed.level)
          ?.noun ?? ""}
        bind:focus
      />
    {/if}
  {/if}

  {#if locating == null && (drawn == null || chamber != null)}
    {#if drawn != null}
      {@render stats(false)}
    {/if}
    <div
      class={cn(
        "@container col-span-12 grid content-start gap-1 bg-card px-1.5 pbs-2 pbe-2.5",
        { "lg:col-span-8": chamber != null && drawn == null },
      )}
    >
      {@render results()}
    </div>
  {/if}

  {#if chamber != null && drawn == null}
    <Seats
      isLinked
      parties={chamber.parties}
      places={chamber.places}
      here={chamber.here}
      place={chamber.places[chamber.here ?? -1]?.name ?? contest.scope.name}
      link={chamber.link}
      size={chamber.size}
      {change}
    />
  {/if}
{/if}

{#if lists != null && !drilled}
  <Lists {lists} />
{/if}

{#if contest.chamber != null && contest.chamber.link == null && !drilled}
  <Members parties={contest.chamber.parties} places={contest.chamber.places} />
{/if}

{#if placed != null}
  {#if drawn == null}
    {@render caveat()}
  {/if}
  {#key here}
    <Places
      places={placed}
      trace={page.url.pathname.slice(1)}
      {span}
      {publishers}
      isMapped={drawn != null}
      deeper={contest.deeper ?? null}
      votes={counted ?? m.col_votes()}
      bind:only={
        () => only,
        (value: number | string | null) =>
          (filter = { at: page.url.href, value })
      }
      bind:focus
    />
  {/key}
{:else if contest.children.length > 0 && !drilled}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.children_title()}</h2>
    <ul class="columns-2 gap-6 text-sm md:columns-4">
      {#each contest.children as child, index (index)}
        <li class="break-inside-avoid py-0.5">
          {#if child.path == null}
            {child.name}
          {:else}
            <Preview class="hover:highlighter" path={child.path}
              >{child.name}</Preview
            >
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{/if}

{#if ballot.length > 0}
  <Ballot {ballot} place={contest.scope.name} />
{/if}

<Cycle
  primaries={cycle.primaries.map((step) => ({
    ...step,
    key: step.id,
    label:
      step.party == null
        ? ""
        : step.until == null
          ? m.cycle_primary({ party: step.party.label })
          : m.cycle_primaries({ party: step.party.label }),
    color: step.party?.color ?? null,
  }))}
  general={cycle.primaries.length === 0 &&
  rounds.some((round) => round.path === cycle.general?.path)
    ? null
    : cycle.general}
  others={cycle.others.map((entry) => ({
    key: entry.party.id,
    label: entry.party.label,
    color: entry.party.color,
    races: entry.races.map((race) => ({ ...race, label: race.place })),
  }))}
  othersTitle={m.cycle_others()}
  day={{
    ...cycle.day,
    groups: cycle.day.groups.flatMap((group, index) =>
      group.path == null
        ? []
        : [
            {
              ...group,
              path: group.path,
              key: `${group.kind}:${index}`,
              label: group.label ?? DAY[group.kind]?.() ?? "",
            },
          ],
    ),
  }}
/>

{#snippet caveat()}
  {#if unreconciled.length > 0}
    <section class="col-span-12 bg-card px-3.5 py-2.5">
      {#each unreconciled as flag, index (index)}
        <p
          class="border-s-[3px] border-destructive ps-2.5 text-sm/snug text-pretty text-muted-foreground max-inline-[72ch]"
        >
          {#if flag.lead != null}
            <strong class="font-semibold text-foreground">{flag.lead}</strong>
          {/if}
          {flag.body}
        </p>
      {/each}
    </section>
  {/if}
{/snippet}

{#if notes.length > 0}
  <section
    id="notes"
    class="col-span-12 scroll-mbs-4 bg-card px-3.5 py-3 md:col-span-6"
  >
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.notes_title()}</h2>
    <ul class="grid gap-1.5 text-sm/snug text-muted-foreground">
      {#each notes as note, index (index)}
        <li class="max-inline-[72ch]">{note}</li>
      {/each}
    </ul>
  </section>
{/if}

{#if contest.sets.length > 0 || contest.citations.length > 0 || checks.length > 0 || boundaries.length > 0}
  <section
    id="sources"
    class={cn("col-span-12 grid content-start gap-4 bg-card px-3.5 py-3", {
      "md:col-span-6": notes.length > 0,
    })}
  >
    {#if contest.sets.length > 0}
      <div>
        <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.sources_title()}</h2>
        <ul class="grid gap-2.5 text-sm/snug">
          {#each sets as set, index (index)}
            <li class="grid gap-0.5">
              <span class="flex flex-wrap items-baseline gap-x-2">
                {#if DEFINITIONS[tier(set)] == null}
                  <span class="font-semibold"
                    >{STAGES[tier(set)]?.() ?? set.stage}</span
                  >
                {:else}
                  <Term
                    class="font-semibold"
                    label={STAGES[tier(set)]?.() ?? set.stage}
                    definition={DEFINITIONS[tier(set)]?.() ?? ""}
                  />
                {/if}
                {#if set.isDefault}
                  <Term
                    class="highlighter px-1 text-xs font-semibold"
                    label={m.source_shown()}
                    definition={m.term_shown()}
                  />
                {/if}
                {#if set.status !== "active"}
                  <span class="text-xs font-semibold text-destructive"
                    >{STATUSES[set.status]?.() ?? set.status}</span
                  >
                {/if}
                {#if set.unreconciled == null && set.completeness != null && set.completeness > 0 && set.completeness < 1}
                  <span class="text-xs text-muted-foreground"
                    >{m.source_completeness({
                      percent: percent(set.completeness),
                    })}</span
                  >
                {/if}
              </span>
              <span
                data-source={[
                  set.source.publisher,
                  set.source.title,
                  set.source.url,
                  set.source.license,
                ]
                  .filter((part) => part != null)
                  .join(", ")}
              >
                {set.source.publisher}:
                {#if set.source.url == null}
                  {set.source.title}
                {:else}
                  <a
                    class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                    href={set.source.url}
                    rel="external noopener">{set.source.title}</a
                  >
                {/if}
              </span>
              {#if set.source.license != null}
                <span class="text-[0.8125rem] text-muted-foreground"
                  >{m.source_license({ license: set.source.license })}</span
                >
              {/if}
              {#if set.source.attribution != null}
                <span class="text-[0.8125rem] text-muted-foreground"
                  >{set.source.attribution}</span
                >
              {/if}
            </li>
          {/each}
        </ul>
      </div>
    {/if}
    {#if contest.citations.length > 0}
      <div>
        <h2 class="mbe-1.5 text-[0.8125rem] font-bold">
          {m.citations_title()}
        </h2>
        <ul class="grid gap-1.5 text-sm/snug">
          {#each contest.citations as citation, index (index)}
            <li>
              {citation.publisher}:
              {#if citation.url == null}
                {citation.title}
              {:else}
                <a
                  class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                  href={citation.url}
                  rel="external noopener">{citation.title}</a
                >
              {/if}
            </li>
          {/each}
        </ul>
      </div>
    {/if}
    {#if boundaries.length > 0}
      <div id="boundaries">
        <h2 class="mbe-1.5 text-[0.8125rem] font-bold">
          {m.boundaries_title()}
        </h2>
        <ul class="grid gap-2.5 text-sm/snug">
          {#each boundaries as entry (entry.scheme)}
            {const shown = $derived(entry.missing.slice(0, 3).join(", "))}
            <li class="grid gap-0.5">
              <span class="flex flex-wrap items-baseline gap-x-2">
                <span class="font-semibold first-letter:uppercase"
                  >{entry.vintage == null
                    ? m.boundaries_scheme({ noun: entry.noun })
                    : m.boundaries_set({
                        noun: entry.noun,
                        vintage: entry.vintage,
                      })}</span
                >
                {#if entry.isApproximate}
                  <Term
                    class="highlighter px-1 text-xs font-semibold"
                    label={m.boundaries_approximate()}
                    definition={m.term_boundaries_approximate()}
                  />
                {/if}
              </span>
              {#each entry.sources as source, index (index)}
                <span>
                  {source.publisher}:
                  {#if source.url == null}
                    {source.title}
                  {:else}
                    <a
                      class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                      href={source.url}
                      rel="external noopener">{source.title}</a
                    >
                  {/if}
                </span>
                {#if source.attribution != null && source.attribution !== source.publisher}
                  <span class="text-[0.8125rem] text-muted-foreground"
                    >{source.attribution}</span
                  >
                {/if}
              {/each}
              {#if entry.missing.length > 0}
                <span class="text-[0.8125rem] text-muted-foreground"
                  >{entry.missing.length > 3
                    ? m.boundaries_missing_more({
                        names: shown,
                        count: entry.missing.length - 3,
                      })
                    : m.boundaries_missing({ names: shown })}</span
                >
              {/if}
            </li>
          {/each}
        </ul>
      </div>
    {/if}
    {#if checks.length > 0}
      <div>
        <h2 class="mbe-1.5 text-[0.8125rem] font-bold">
          <Term label={m.checks_title()} definition={m.term_checks()} />
        </h2>
        <ul class="grid gap-1.5 text-sm/snug">
          {#each checks as check (check.code)}
            <li>
              <details class="group">
                <summary
                  class="cursor-pointer marker:text-muted-foreground hover:highlighter"
                  >{check.summary}</summary
                >
                <ul
                  class="mbs-1.5 grid gap-1 overflow-y-auto border-s ps-3 text-[0.8125rem] text-muted-foreground max-block-64"
                >
                  {#each check.units as unit, index (index)}
                    <li>{unit}</li>
                  {/each}
                </ul>
              </details>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  </section>
{/if}

{#snippet stats(compact: boolean)}
  {#if contest.current != null || contest.turnout != null}
    <dl
      class={cn(
        "flex flex-wrap items-baseline gap-x-7 gap-y-1.5 bg-card px-3.5 py-3",
        { "col-span-12": !compact },
      )}
    >
      {#if lead != null}
        <div
          class="flex items-baseline gap-2 min-inline-0"
          style:--party={swatch(lead.row.color, lead.row.key)}
        >
          <dt class="text-[0.8125rem] text-muted-foreground">
            <Term
              label={m.col_margin()}
              definition={members == null
                ? m.term_margin()
                : m.term_margin_last()}
            />
          </dt>
          <dd class="flex items-baseline gap-2 min-inline-0">
            <span class="font-figures text-xl font-[760] tracking-[-0.015em]"
              >{points(lead.margin)}</span
            >
            <Name
              class="truncate text-sm font-semibold min-inline-0"
              label={lead.row.label}
              color={swatch(lead.row.color, lead.row.key)}
              person={lead.row.current?.person}
              party={lead.row.current?.party}
            />
          </dd>
        </div>
      {/if}
      {#if contest.current != null}
        <div class="flex items-baseline gap-2">
          <dt class="text-[0.8125rem] text-muted-foreground">
            <Term
              label={contest.ballot == null
                ? (counted ?? m.stat_valid())
                : m.stat_votes_cast()}
              definition={contest.ballot == null
                ? (DEFINITIONS[contest.current.measure]?.() ?? m.term_valid())
                : m.term_votes_cast({ count: String(contest.ballot) })}
            />
          </dt>
          <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
            {count(contest.current.valid)}
          </dd>
        </div>
        <div class="flex items-baseline gap-2">
          <dt class="text-[0.8125rem] text-muted-foreground">
            <Term label={m.stat_options()} definition={m.term_options()} />
          </dt>
          <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
            {contest.rows.filter((row) => row.current != null).length}
          </dd>
        </div>
      {/if}
      {#if single != null}
        <div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <dt class="sr-only">
            {m.stat_seat()}
          </dt>
          <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
            1 <span class="text-sm font-normal text-muted-foreground"
              >{m.seats_of_total({ count: count(single.total) })}</span
            >
          </dd>
          <dd class="text-[0.8125rem] text-muted-foreground">
            {const pieces = $derived(
              m
                .seats_single_national({
                  count: single.winner.seats,
                  party: "\u0001party\u0001",
                  where: "\u0001where\u0001",
                })
                .split("\u0001"),
            )}
            {#each pieces as piece, index (index)}{#if index % 2 === 1 && piece === "party"}<Name
                  class="text-foreground"
                  label={single.winner.short ?? single.winner.label}
                  color={swatch(single.winner.color, single.winner.key)}
                  party={single.party}
                />{:else if index % 2 === 1 && piece === "where"}{#if single.link.path != null}<Preview
                    class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
                    path={single.link.path}>{single.link.name}</Preview
                  >{:else}{single.link.name}{/if}{:else}{piece}{/if}{/each}
          </dd>
        </div>
      {/if}
      {#if contest.turnout != null}
        {#if contest.current == null}
          <div class="flex items-baseline gap-2">
            <dt class="text-[0.8125rem] text-muted-foreground">
              {m.stat_registered()}
            </dt>
            <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
              {count(contest.turnout.registered)}
            </dd>
          </div>
          <div class="flex items-baseline gap-2">
            <dt class="text-[0.8125rem] text-muted-foreground">
              {m.stat_voters()}
            </dt>
            <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
              {count(contest.turnout.voters)}
            </dd>
          </div>
        {/if}
        <div class="flex items-baseline gap-2">
          <dt class="text-[0.8125rem] text-muted-foreground">
            <Term
              label={m.stat_turnout()}
              definition={contest.turnout.basis === "rejected"
                ? m.term_turnout_rejected()
                : contest.turnout.basis === "valid"
                  ? m.term_turnout_valid()
                  : m.term_turnout()}
            />
          </dt>
          <dd class="flex flex-wrap items-baseline gap-x-2">
            <span class="font-figures text-xl font-[760] tracking-[-0.015em]"
              >{percent(
                contest.turnout.voters / contest.turnout.registered,
              )}</span
            >
            {#if contest.current != null}
              <span
                class="font-figures text-xs text-muted-foreground tabular-nums"
                >{m.stat_turnout_count({
                  voters: count(contest.turnout.voters),
                  registered: count(contest.turnout.registered),
                })}</span
              >
            {/if}
            {#if contest.turnout.earlier != null && contest.target?.year != null}
              <span class="font-figures text-xs font-semibold tabular-nums"
                >{m.turnout_since({
                  points: points(
                    contest.turnout.voters / contest.turnout.registered -
                      contest.turnout.earlier.voters /
                        contest.turnout.earlier.registered,
                  ),
                  year: String(contest.target.year),
                })}</span
              >
            {/if}
            {#if contest.turnout.reporting != null && contest.turnout.of != null && contest.turnout.reporting < contest.turnout.of}
              <span class="text-xs text-muted-foreground"
                >{m.turnout_partial({
                  reporting: String(contest.turnout.reporting),
                  of: String(contest.turnout.of),
                })}</span
              >
            {/if}
          </dd>
        </div>
      {/if}
    </dl>
  {/if}
{/snippet}

{#snippet results()}
  {#if contest.current != null}
    <Results
      rows={contest.rows}
      trace={page.url.pathname.slice(1)}
      {span}
      votes={counted ?? m.col_votes()}
      seats={seated ? (SEATS[contest.kind]?.() ?? m.col_seats()) : null}
    />
    {#if captions.length > 0}
      <p class="px-2 text-[0.8125rem] text-muted-foreground">
        <a
          class="underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={secondary && notes.length > 0 ? "#notes" : "#sources"}
          >{captions.join("; ")}</a
        >
      </p>
    {/if}
  {/if}
{/snippet}
