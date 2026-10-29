<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { arrange, ARRIVALS, DEPARTURES } from "#lib/caucus.js";
  import Measured from "#lib/components/measured.svelte";
  import Name from "#lib/components/name.svelte";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

  const {
    terms,
    parties,
    governments,
    interim = [],
    isShare = false,
    selected = null,
    onselect,
    link,
  }: {
    terms: Array<{
      start: string;
      end: string | null;
      total: number;
      size: number | null;
      apportioned: number | null;
      seceded: number;
      isSeated: boolean;
      isPartial: boolean;
      parties: Array<{
        party: string | null;
        seats: number;
        group?: string | null;
      }>;
      election: { name: string; path: string | null; date: string } | null;
      title?: string | null;
      names?: Record<string, { label: string; short: string | null }>;
      near?: Record<string, string | null>;
      steps?: Array<{
        date: string;
        parties: Array<{
          party: string | null;
          seats: number;
          group?: string | null;
        }>;
        near?: Record<string, string | null>;
        moves: Array<{
          kind: "in" | "out" | "switch" | "rename";
          from: string | null;
          to: string | null;
          cause: string | null;
          members: number;
        }>;
      }>;
    }>;
    parties: Array<{
      id: string;
      label: string;
      short: string | null;
      color: string | null;
      lean: number | null;
      isStriped: boolean;
      isMade: boolean;
    }>;
    governments: Array<{
      name: string;
      head: { id: string; name: string } | null;
      party: string | null;
      from: string;
      to: string | null;
      isCaretaker: boolean;
    }>;
    interim?: Array<{ name: string; from: string | null; to: string | null }>;
    isShare?: boolean;
    selected?: number | null;
    onselect?: (index: number) => void;
    link?: (index: number) => string | null;
  } = $props();

  const uid = $props.id();
  const PAD = { top: 10, side: 1, axis: 22 };

  let width = $state(0);
  let hovered = $state<number | null>(null);
  let pointer = $state<number | null>(null);
  let seated = $state<string | null>(null);

  const today = new Date().toISOString().slice(0, 10);
  const blocs = $derived(
    new Map<string, { group: string; party: string | null }>(
      terms.flatMap((term) =>
        [...term.parties, ...(term.steps ?? []).flatMap((step) => step.parties)]
          .filter((entry) => entry.group != null)
          .map((entry) => [
            `${entry.group}/${entry.party ?? "none"}`,
            { group: entry.group ?? "", party: entry.party },
          ]),
      ),
    ),
  );
  const sheets = $derived.by(() => {
    if (blocs.size === 0) {
      return terms;
    }
    const fold = (entry: {
      party: string | null;
      seats: number;
      group?: string | null;
    }) =>
      entry.group == null
        ? entry
        : { ...entry, party: `${entry.group}/${entry.party ?? "none"}` };
    return terms.map((term) => ({
      ...term,
      parties: term.parties.map(fold),
      steps: term.steps?.map((step) => ({
        ...step,
        parties: step.parties.map(fold),
      })),
    }));
  });
  const blocks = $derived.by(() => {
    const own = new Map(parties.map((party) => [party.id, party]));
    return new Map(
      Map.groupBy(
        terms.flatMap((term) =>
          term.parties.filter((entry) => entry.group != null),
        ),
        (entry) => entry.group ?? "",
      )
        .entries()
        .map(([group, entries]) => {
          let seats = 0;
          let weight = 0;
          let sum = 0;
          for (const entry of entries) {
            const lean = own.get(entry.party ?? "")?.lean;
            seats += entry.seats;
            if (lean != null) {
              weight += entry.seats;
              sum += lean * entry.seats;
            }
          }
          return { group, seats, lean: weight > 0 ? sum / weight : null };
        })
        .toArray()
        .toSorted((a, b) => b.seats - a.seats)
        .map(
          (at, index) =>
            [
              at.group,
              own.get(at.group)?.lean ?? at.lean ?? 1e6 + index,
            ] as const,
        ),
    );
  });
  const info = $derived.by(() => {
    const own = new Map(parties.map((party) => [party.id, party]));
    return new Map([
      ...own,
      ...[...blocs].map(([key, bloc]) => {
        const party = own.get(bloc.party ?? "");
        return [
          key,
          {
            id: key,
            label: party?.label ?? "",
            short: party?.short ?? null,
            color: null,
            lean: blocks.get(bloc.group) ?? null,
            isStriped: false,
            isMade: party == null,
          },
        ] as const;
      }),
    ]);
  });
  const tint = (key: string | null) => {
    const party = blocs.get(key ?? "")?.group ?? key;
    return party == null
      ? swatch(null, "")
      : swatch(
          info.get(party)?.color ?? null,
          /^[0-9a-f]{8}-/.test(party) ? `party:${party}` : party,
        );
  };
  const label = (
    party: string | null,
    names?: Record<string, { label: string; short: string | null }>,
  ) => {
    if (party == null || party.endsWith("/none")) {
      return m.record_none();
    }
    const found = info.get(party);
    const dated = names?.[blocs.get(party)?.party ?? party];
    return dated == null
      ? (found?.short ?? found?.label ?? "")
      : (dated.short ??
          (dated.label === found?.label ? found.short : null) ??
          dated.label);
  };
  const band = $derived(governments.length > 0 ? 18 : 0);
  const height = $derived(width < 640 ? 230 : 320);
  const inner = $derived(height - PAD.top - PAD.axis - band);
  const low = $derived(Date.parse(terms[0]?.start ?? today));
  const high = $derived(
    Math.max(
      Date.parse(terms.at(-1)?.end ?? today),
      Date.parse(terms.at(-1)?.start ?? today) + 1,
    ),
  );
  const sx = (value: number) =>
    PAD.side +
    ((value - low) / Math.max(high - low, 1)) * (width - 2 * PAD.side);
  const peak = $derived(
    Math.max(
      1,
      ...terms.map((term) =>
        Math.max(term.total + term.seceded, term.size ?? 0),
      ),
    ),
  );
  const order = $derived.by(() => {
    const held = new Map(
      Map.groupBy(
        sheets.flatMap((term) => [
          ...term.parties,
          ...(term.steps ?? []).flatMap((step) => step.parties),
        ]),
        (entry) => entry.party,
      )
        .entries()
        .map(([party, entries]) => {
          let sum = 0;
          for (const entry of entries) {
            sum += entry.seats;
          }
          return [party, sum] as const;
        }),
    );
    return [...held.keys()].toSorted(
      (a, b) =>
        Number(a == null) - Number(b == null) ||
        (info.get(a ?? "")?.lean ?? Infinity) -
          (info.get(b ?? "")?.lean ?? Infinity) ||
        (held.get(b) ?? 0) - (held.get(a) ?? 0),
    );
  });
  const columns = $derived(
    sheets.map((term, index) => {
      const x0 = sx(Date.parse(term.start));
      const x1 = sx(Date.parse(term.end ?? today));
      const full =
        term.seceded > 0 ? (term.apportioned ?? term.size) : term.size;
      const empty =
        term.isSeated && full != null
          ? Math.max(0, full - term.total - term.seceded)
          : 0;
      const whole = Math.max(term.total + term.seceded + empty, 1);
      const unit = isShare ? inner / whole : inner / peak;
      const stack = (
        parties: Array<{ party: string | null; seats: number }>,
        near: Record<string, string | null> = {},
      ) => {
        const seats = new Map(
          parties.map((entry) => [entry.party, entry.seats]),
        );
        const entry = (party: string | null) => ({
          key: party ?? "none",
          party,
          lean: info.get(party ?? "")?.lean ?? null,
          near: party == null ? null : (near[party] ?? null),
          seats: seats.get(party) ?? 0,
        });
        let below = 0;
        const layers = new Map<string | null, { y0: number; y1: number }>();
        for (const { party, seats: value } of arrange(
          [...seats.keys()].map(entry).filter((item) => item.seats > 0),
        )) {
          layers.set(party, {
            y0: PAD.top + inner - below * unit,
            y1: PAD.top + inner - (below + value) * unit,
          });
          below += value;
        }
        let top = PAD.top + inner - below * unit;
        for (const { party } of arrange(order.map(entry)).toReversed()) {
          const at = layers.get(party);
          if (at == null) {
            layers.set(party, { y0: top, y1: top });
          } else {
            top = at.y0;
          }
        }
        return { layers, below };
      };
      const { below } = stack(term.parties, term.near);
      const opening = {
        from: term.start,
        parties: term.parties,
        near: term.near,
        step: null,
      };
      const marks = [
        opening,
        ...(term.steps ?? []).map((step) => ({
          from: step.date,
          parties: step.parties,
          near: step.near,
          step,
        })),
      ];
      const parts = marks.map((part, at) => {
        const after = marks[at + 1];
        const { layers, below: seated } = stack(part.parties, part.near);
        const spans = new Map(
          Map.groupBy(
            [...layers].filter(([key]) => blocs.has(key ?? "")),
            ([key]) => blocs.get(key ?? "")?.group ?? "",
          )
            .entries()
            .map(
              ([group, entries]) =>
                [
                  group,
                  {
                    y0: Math.max(...entries.map(([, at]) => at.y0)),
                    y1: Math.min(...entries.map(([, at]) => at.y1)),
                  },
                ] as const,
            ),
        );
        const open =
          term.isSeated && full != null
            ? Math.max(0, full - seated - term.seceded)
            : 0;
        return {
          ...part,
          x0: at === 0 ? x0 : sx(Date.parse(part.from)),
          x1: after == null ? x1 : sx(Date.parse(after.from)),
          to: after?.from ?? term.end,
          stack: layers,
          spans,
          empty: open,
          vacant: {
            y0: PAD.top + inner - (seated + term.seceded) * unit,
            y1: PAD.top + inner - (seated + term.seceded + open) * unit,
          },
        };
      });
      const n = term.size ?? term.total;
      const majority = Math.floor(n / 2) + 1;
      return {
        index,
        term,
        x0,
        x1,
        parts,
        whole,
        seceded: {
          y0: PAD.top + inner - below * unit,
          y1: PAD.top + inner - (below + term.seceded) * unit,
        },
        line:
          PAD.top +
          inner -
          (isShare ? (majority / Math.max(n, 1)) * inner : majority * unit),
      };
    }),
  );
  const slices = $derived(
    columns.flatMap((column) =>
      column.parts.map((part) => ({ ...part, column: column.index })),
    ),
  );
  const groups = $derived(
    [...blocks.keys()].toSorted(
      (a, b) => (blocks.get(a) ?? 0) - (blocks.get(b) ?? 0),
    ),
  );
  const bands = $derived(
    [
      ...order.map((party) => ({ party, isGroup: false })),
      ...groups.map((party) => ({ party, isGroup: true })),
    ].map(({ party, isGroup }) => {
      const runs = slices.map((part, index) => {
        const before = slices[index - 1];
        const after = slices[index + 1];
        const column = columns[part.column];
        const ease = Math.min(
          5,
          ((column?.x1 ?? 0) - (column?.x0 ?? 0)) * 0.22,
          (part.x1 - part.x0) / 2,
        );
        const isInBefore = before?.column === part.column;
        const isInAfter = after?.column === part.column;
        const joinsBefore =
          before != null && (isInBefore || part.x0 - before.x1 < 1);
        const joinsAfter =
          after != null && (isInAfter || after.x0 - part.x1 < 1);
        return {
          a: part.x0 + (joinsBefore && !isInBefore ? ease : 0),
          b: part.x1 - (joinsAfter && !isInAfter ? ease : 0),
          edge: part.x1,
          joinsAfter,
          ...((isGroup
            ? part.spans.get(party ?? "")
            : part.stack.get(party)) ?? { y0: 0, y1: 0 }),
        };
      });
      const joined: Array<typeof runs> = [];
      for (const run of runs) {
        const group = joined.at(-1);
        if (group == null || group.at(-1)?.joinsAfter !== true) {
          joined.push([run]);
        } else {
          group.push(run);
        }
      }
      let d = "";
      for (const group of joined) {
        for (const [index, run] of group.entries()) {
          const before = group[index - 1];
          d +=
            before == null
              ? `M${run.a},${run.y1}`
              : `C${before.edge},${before.y1} ${before.edge},${run.y1} ${run.a},${run.y1}`;
          d += `L${run.b},${run.y1}`;
        }
        for (const [index, run] of group.entries().toArray().toReversed()) {
          const after = group[index + 1];
          d +=
            after == null
              ? `L${run.b},${run.y0}`
              : `C${run.edge},${after.y0} ${run.edge},${run.y0} ${run.b},${run.y0}`;
          d += `L${run.a},${run.y0}`;
        }
        d += "Z";
      }
      return { party, isGroup, d: d.replaceAll(/(\.\d)\d+/g, "$1") };
    }),
  );
  const majority = $derived.by(() => {
    let d = "";
    for (const [index, column] of columns.entries()) {
      if (column.term.isPartial) {
        continue;
      }
      const isJoined =
        index > 0 &&
        columns[index - 1]?.term.isPartial === false &&
        column.x0 - (columns[index - 1]?.x1 ?? 0) < 1;
      d += `${isJoined ? "L" : "M"}${column.x0},${column.line}L${column.x1},${column.line}`;
    }
    return d;
  });
  const names = $derived.by(() => {
    const placed: Array<{ party: string; x: number; y: number; text: string }> =
      [];
    for (const party of [...groups, ...order]) {
      if (party == null || blocs.has(party)) {
        continue;
      }
      let best: { x: number; y: number; room: number; text: string } | null =
        null;
      for (const part of slices) {
        const text = label(party, columns[part.column]?.term.names);
        const at = part.spans.get(party) ?? part.stack.get(party);
        const thick = at == null ? 0 : at.y0 - at.y1;
        const wide = part.x1 - part.x0;
        if (at != null && thick >= 15 && wide >= text.length * 6.6 + 12) {
          const room = thick * Math.min(wide, 220);
          if (best == null || room > best.room) {
            best = {
              x: (part.x0 + part.x1) / 2,
              y: (at.y0 + at.y1) / 2,
              room,
              text,
            };
          }
        }
      }
      if (best != null) {
        placed.push({ party, x: best.x, y: best.y, text: best.text });
      }
    }
    return placed;
  });
  const years = $derived.by(() => {
    const first = new Date(low).getUTCFullYear();
    const last = new Date(high).getUTCFullYear();
    const every =
      [1, 2, 5, 10, 20, 25, 50].find(
        (size) => ((last - first) / size) * 48 < width,
      ) ?? 50;
    const found: Array<{ year: number; x: number }> = [];
    for (
      let year = Math.ceil((first + 1) / every) * every;
      year <= last;
      year += every
    ) {
      found.push({ year, x: sx(Date.UTC(year, 0, 1)) });
    }
    return found;
  });
  const cabinets = $derived(
    governments.flatMap((cabinet) => {
      const from = Math.max(low, Date.parse(cabinet.from));
      const to = Date.parse(cabinet.to ?? today);
      return to <= from
        ? []
        : [
            {
              ...cabinet,
              x: sx(from),
              w: Math.max(sx(to) - sx(from) - 0.5, 0.6),
            },
          ];
    }),
  );
  const lit = $derived(columns[hovered ?? -1] ?? null);
  const part = $derived(
    lit == null
      ? null
      : ((pointer == null
          ? null
          : lit.parts.findLast(
              (entry) => pointer != null && pointer >= entry.x0,
            )) ??
          lit.parts[0] ??
          null),
  );
  const ranked = $derived(
    Map.groupBy(
      part?.parties ?? [],
      (entry) => blocs.get(entry.party ?? "")?.group ?? entry.party,
    )
      .entries()
      .map(([party, members]) => {
        let seats = 0;
        for (const member of members) {
          seats += member.seats;
        }
        return {
          party,
          seats,
          members: blocs.has(members[0]?.party ?? "")
            ? members.toSorted((a, b) => b.seats - a.seats)
            : [],
        };
      })
      .toArray()
      .toSorted((a, b) => b.seats - a.seats),
  );
  const steps = $derived(
    slices.filter((entry) => entry.step != null).map((entry) => entry.x0),
  );
  const current = $derived(selected ?? hovered ?? columns.length - 1);
  const pick = (index: number) => {
    if (selected != null) {
      onselect?.(index);
      return;
    }
    const path = link?.(index);
    if (path != null) {
      void goto(resolve("/[...path]", { path }));
    }
  };
  const sitting = $derived(
    cabinets.find((cabinet) => cabinet.from === seated) ?? null,
  );
  const shown = $derived((lit ?? columns[current])?.term.names);
  const legend = $derived(
    [...groups, ...order.filter((party) => !blocs.has(party ?? ""))]
      .filter(
        (party) =>
          info.get(party ?? "")?.isStriped === true ||
          terms.some((term) => {
            let seats = 0;
            for (const entry of term.parties) {
              if ((entry.group ?? entry.party) === party) {
                seats += entry.seats;
              }
            }
            return seats >= 0.06 * term.total;
          }),
      )
      .filter(
        (party, index, all) =>
          all.findIndex(
            (other) => label(other, shown) === label(party, shown),
          ) === index,
      )
      .slice(0, 14),
  );

  const along = (event: MouseEvent & { currentTarget: SVGSVGElement }) => {
    const box = event.currentTarget.getBoundingClientRect();
    return ((event.clientX - box.left) / box.width) * width;
  };
  const locate = (event: MouseEvent & { currentTarget: SVGSVGElement }) => {
    const x = along(event);
    const found = columns.findIndex(
      (column) => x >= column.x0 && x < column.x1,
    );
    if (found >= 0) {
      return found;
    }
    return x < (columns[0]?.x0 ?? 0)
      ? 0
      : x >= (columns.at(-1)?.x1 ?? 0)
        ? columns.length - 1
        : null;
  };
  const gap = $derived.by(() => {
    if (pointer == null || hovered != null) {
      return null;
    }
    const after = columns.findIndex((column) => column.x0 > (pointer ?? 0));
    const before = columns[after - 1];
    const next = columns[after];
    if (before == null || next == null) {
      return null;
    }
    const from = before.term.end ?? today;
    const to = next.term.start;
    const slack = 90 * 864e5;
    return {
      from,
      to,
      x0: before.x1,
      x1: next.x0,
      bodies: interim
        .filter(
          (body) =>
            body.from != null &&
            Date.parse(body.from) >= Date.parse(from) - slack &&
            Date.parse(body.to ?? today) <= Date.parse(to) + slack,
        )
        .map((body) => body.name),
    };
  });
</script>

<Measured
  bind:width
  class="relative min-inline-0"
  reserve="clamp(230px, (100vw - 640px) * 100, 320px)"
>
  <svg
    class={cn(
      "block touch-pan-y overflow-visible rounded-sm outline-none block-auto max-inline-full focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring",
      {
        "cursor-pointer":
          (selected != null && onselect != null) ||
          (hovered != null && link?.(hovered) != null),
      },
    )}
    viewBox="0 0 {width} {height}"
    {width}
    {height}
    role="slider"
    tabindex="0"
    aria-label={m.chamber_term_pick()}
    aria-valuemin={0}
    aria-valuemax={Math.max(columns.length - 1, 0)}
    aria-valuenow={current}
    aria-valuetext={terms[current]?.election?.name ??
      terms[current]?.start ??
      ""}
    onpointermove={(event) => {
      hovered = locate(event);
      pointer = along(event);
    }}
    onpointerleave={() => {
      hovered = null;
      pointer = null;
    }}
    onblur={() => {
      if (selected == null) {
        hovered = null;
      }
    }}
    onclick={(event) => {
      const index = locate(event);
      if (index != null) {
        pick(index);
      }
    }}
    onkeydown={(event) => {
      const moves: Record<string, number> = {
        ArrowRight: 1,
        ArrowUp: 1,
        ArrowLeft: -1,
        ArrowDown: -1,
        Home: -Infinity,
        End: Infinity,
      };
      const move = moves[event.key];
      if (move != null) {
        event.preventDefault();
        pointer = null;
        const next = Math.min(columns.length - 1, Math.max(0, current + move));
        if (selected == null) {
          hovered = next;
        } else {
          onselect?.(next);
        }
      } else if (event.key === "Enter" && selected == null) {
        pick(current);
      }
    }}
  >
    <defs>
      <pattern
        id="{uid}-vacant"
        width="4"
        height="4"
        patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)"
      >
        <rect width="1" height="4" class="fill-muted-foreground" />
      </pattern>
      <pattern
        id="{uid}-stripe"
        width="5"
        height="5"
        patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)"
      >
        <rect width="1.5" height="5" class="fill-card" />
      </pattern>
    </defs>
    <g>
      {#each slices.filter((entry) => entry.empty > 0) as entry, index (index)}
        <rect
          x={entry.x0}
          y={entry.vacant.y1}
          width={Math.max(entry.x1 - entry.x0, 0.5)}
          height={entry.vacant.y0 - entry.vacant.y1}
          fill="url(#{uid}-vacant)"
          opacity="0.55"
        />
      {/each}
      {#each columns.filter((column) => column.term.seceded > 0) as column (column.index)}
        <rect
          x={column.x0}
          y={column.seceded.y1}
          width={Math.max(column.x1 - column.x0, 0.5)}
          height={column.seceded.y0 - column.seceded.y1}
          class="fill-muted stroke-muted-foreground"
          stroke-width="0.75"
          stroke-dasharray="3 2"
        />
      {/each}
      {#each bands as entry (`${entry.isGroup}:${entry.party ?? "none"}`)}
        <path
          d={entry.d}
          fill={entry.isGroup ? "none" : tint(entry.party)}
          stroke="var(--card)"
          stroke-width={entry.isGroup ? 1.75 : 0.5}
          stroke-linejoin="round"
          class={cn({ "pointer-events-none": entry.isGroup })}
        />
        {#if !entry.isGroup && info.get(entry.party ?? "")?.isStriped === true}
          <path
            d={entry.d}
            fill="url(#{uid}-stripe)"
            opacity="0.6"
            class="pointer-events-none"
          />
        {/if}
      {/each}
    </g>
    {#if lit != null}
      <path
        d="M{PAD.side},{PAD.top}H{lit.x0}V{PAD.top +
          inner}H{PAD.side}ZM{lit.x1},{PAD.top}H{width}V{PAD.top +
          inner}H{lit.x1}Z"
        class="pointer-events-none fill-card"
        opacity="0.5"
      />
    {/if}
    {#each columns.slice(1) as column (column.index)}
      <line
        x1={column.x0}
        x2={column.x0}
        y1={PAD.top}
        y2={PAD.top + inner}
        class="pointer-events-none stroke-foreground"
        stroke-width="0.75"
        stroke-opacity="0.45"
      />
    {/each}
    {#each steps as x, index (index)}
      <line
        x1={x}
        x2={x}
        y1={PAD.top + inner + 1}
        y2={PAD.top + inner + 4}
        class="pointer-events-none stroke-muted-foreground"
        stroke-width="0.75"
      />
    {/each}
    {#each names as name (name.party)}
      <text
        x={name.x}
        y={name.y}
        dy="0.35em"
        text-anchor="middle"
        class="pointer-events-none text-[0.6875rem] font-semibold"
        style:fill={`color(from ${tint(name.party)} xyz-d65 calc(0.9505 * clamp(0, (0.179 - y) * 1e6, 1)) clamp(0, (0.179 - y) * 1e6, 1) calc(1.089 * clamp(0, (0.179 - y) * 1e6, 1)))`}
        >{name.text}</text
      >
    {/each}
    <path
      d={majority}
      fill="none"
      class="pointer-events-none stroke-card"
      stroke-width="3"
      stroke-opacity="0.6"
    />
    <path
      d={majority}
      fill="none"
      class="pointer-events-none stroke-foreground"
      stroke-width="1.25"
      stroke-dasharray="4 3"
    />
    {#each [columns[selected ?? -1]] as column (column?.index)}
      {#if column != null}
        <rect
          x={column.x0}
          y={PAD.top - 4}
          width={Math.max(column.x1 - column.x0, 1.5)}
          height={inner + 4}
          class="pointer-events-none fill-none stroke-foreground"
          stroke-width="1.5"
        />
        <path
          d="M{(column.x0 + column.x1) / 2 - 5},{PAD.top - 10}l5,6l5,-6z"
          class="pointer-events-none fill-foreground"
        />
      {/if}
    {/each}
    {#each cabinets as cabinet (cabinet.from)}
      <rect
        x={cabinet.x}
        y={PAD.top + inner + 6}
        width={cabinet.w}
        height="9"
        fill={tint(cabinet.party)}
        opacity={seated == null || seated === cabinet.from
          ? cabinet.isCaretaker
            ? 0.5
            : 1
          : 0.3}
        class="cursor-default"
        role="presentation"
        onclick={(event) => event.stopPropagation()}
        onpointerenter={(event) => {
          if (event.pointerType === "mouse") {
            seated = cabinet.from;
          }
        }}
        onpointerleave={(event) => {
          if (event.pointerType === "mouse") {
            seated = null;
          }
        }}
        onpointerup={(event) => {
          if (event.pointerType !== "mouse") {
            seated = seated === cabinet.from ? null : cabinet.from;
          }
        }}
      />
    {/each}
    {#each years as year (year.year)}
      <line
        x1={year.x}
        x2={year.x}
        y1={height - PAD.axis + 2}
        y2={height - PAD.axis + 6}
        class="stroke-muted-foreground"
      />
      <text
        x={year.x}
        y={height - 4}
        text-anchor="middle"
        class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
        >{year.year}</text
      >
    {/each}
  </svg>
  {#if gap != null}
    <div
      class="pointer-events-none absolute grid gap-1 rounded-sm bg-card/95 px-2.5 py-2 text-xs shadow-menu ring-1 ring-border inline-60"
      style:inset-inline-start="{gap.x1 + 248 > width
        ? Math.max(0, gap.x0 - 248)
        : gap.x1 + 8}px"
      style:inset-block-start="{PAD.top}px"
    >
      <span class="text-[0.8125rem] font-semibold">{m.makeup_gap()}</span>
      <span class="font-figures text-muted-foreground tabular-nums"
        >{date(gap.from)} – {date(gap.to)}</span
      >
      {#if gap.bodies.length > 0}
        <span
          >{m.makeup_gap_instead({
            bodies: new Intl.ListFormat(getLocale(), {
              type: "conjunction",
            }).format(gap.bodies),
          })}</span
        >
      {/if}
    </div>
  {/if}
  {#if lit != null && part != null}
    {const leader = $derived(ranked[0])}
    <div
      class="pointer-events-none absolute grid gap-1 rounded-sm bg-card/95 px-2.5 py-2 text-xs shadow-menu ring-1 ring-border inline-60"
      style:inset-inline-start="{lit.x1 + 248 > width
        ? Math.max(0, lit.x0 - 248)
        : lit.x1 + 8}px"
      style:inset-block-start="{PAD.top}px"
    >
      <span class="text-[0.8125rem] font-semibold"
        >{lit.term.election?.name ?? date(lit.term.start)}</span
      >
      {#if lit.term.title != null && lit.term.title !== terms.at(-1)?.title}
        <span class="text-muted-foreground">{lit.term.title}</span>
      {/if}
      <span class="font-figures text-muted-foreground tabular-nums"
        >{date(part.from)} – {part.to == null
          ? m.governments_present()
          : date(part.to)}</span
      >
      {#if part.step != null}
        <ul class="grid gap-0.5 border-be pbe-1">
          {#each part.step.moves.slice(0, 5) as move, index (index)}
            <li class="flex items-center justify-between gap-3">
              <span class="flex items-center gap-1 truncate">
                {#if move.kind === "switch"}
                  <span
                    class="shrink-0 rounded-xs block-2 inline-2"
                    style:background={tint(move.from)}
                  ></span>
                  <span class="truncate"
                    >{label(move.from, lit.term.names)}</span
                  >
                  <span aria-hidden="true">→</span>
                {/if}
                <span
                  class="shrink-0 rounded-xs block-2 inline-2"
                  style:background={tint(move.to ?? move.from)}
                ></span>
                <span class="truncate"
                  >{label(move.to ?? move.from, lit.term.names)}</span
                >
                {#if move.kind !== "switch"}
                  <span class="truncate text-muted-foreground"
                    >{(move.kind === "in"
                      ? ARRIVALS[move.cause ?? ""]
                      : DEPARTURES[move.cause ?? ""])?.() ??
                      (move.kind === "in"
                        ? m.chamber_joined()
                        : m.chamber_left())}</span
                  >
                {/if}
              </span>
              <span class="font-figures tabular-nums"
                >{move.kind === "out"
                  ? "−"
                  : move.kind === "in"
                    ? "+"
                    : ""}{count(move.members)}</span
              >
            </li>
          {/each}
          {#if part.step.moves.length > 5}
            <li class="text-muted-foreground">
              {m.count_more({ count: count(part.step.moves.length - 5) })}
            </li>
          {/if}
        </ul>
      {/if}
      <ul class="grid gap-0.5">
        {#each ranked.slice(0, blocs.size > 0 ? 4 : 6) as entry (entry.party ?? "none")}
          <li class="flex items-center justify-between gap-3">
            <span class="flex items-center gap-1.5 truncate">
              <span
                class="shrink-0 rounded-xs block-2.5 inline-2.5"
                style:background={tint(entry.party)}
              ></span>
              <span class="truncate">{label(entry.party, lit.term.names)}</span>
            </span>
            <span class="font-figures tabular-nums"
              ><span class="font-semibold">{count(entry.seats)}</span>
              <span class="text-muted-foreground"
                >{percent(entry.seats / lit.whole)}</span
              ></span
            >
          </li>
          {#each entry.members.slice(0, 2) as member (member.party ?? "none")}
            <li
              class="flex items-center justify-between gap-3 ps-4 text-[0.6875rem] text-muted-foreground"
            >
              <span class="truncate">{label(member.party, lit.term.names)}</span
              >
              <span class="font-figures tabular-nums"
                >{count(member.seats)}</span
              >
            </li>
          {/each}
          {#if entry.members.length > 2}
            <li class="ps-4 text-[0.6875rem] text-muted-foreground">
              {m.count_more({ count: count(entry.members.length - 2) })}
            </li>
          {/if}
        {/each}
        {#if lit.term.seceded > 0}
          <li class="flex items-center justify-between gap-3">
            <span class="flex items-center gap-1.5 truncate">
              <span
                class="shrink-0 rounded-xs border border-dashed border-muted-foreground bg-muted block-2.5 inline-2.5"
              ></span>
              <span class="truncate">{m.seats_seceded()}</span>
            </span>
            <span class="font-figures tabular-nums"
              ><span class="font-semibold">{count(lit.term.seceded)}</span>
              <span class="text-muted-foreground"
                >{percent(lit.term.seceded / lit.whole)}</span
              ></span
            >
          </li>
        {/if}
        {#if part.empty > 0}
          <li class="flex items-center justify-between gap-3">
            <span class="flex items-center gap-1.5 truncate">
              <span
                class="shrink-0 rounded-xs ring-1 ring-muted-foreground block-2.5 inline-2.5 ring-inset"
              ></span>
              <span class="truncate">{m.seats_vacant()}</span>
            </span>
            <span class="font-figures tabular-nums"
              ><span class="font-semibold">{count(part.empty)}</span>
              <span class="text-muted-foreground"
                >{percent(part.empty / lit.whole)}</span
              ></span
            >
          </li>
        {/if}
      </ul>
      {#if lit.term.seceded > 0 && lit.term.size != null}
        <span class="text-muted-foreground"
          >{m.seats_official({
            count: count(lit.term.size),
            majority: count(Math.floor(lit.term.size / 2) + 1),
          })}</span
        >
      {:else if leader != null}
        <span class="text-muted-foreground"
          >{m.record_seats({
            seats: count(leader.seats),
            total: count(lit.term.size ?? lit.term.total),
          })}</span
        >
      {/if}
    </div>
  {/if}
</Measured>
{#if cabinets.length > 0}
  <p
    class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[0.8125rem] min-block-5"
    aria-live="polite"
  >
    {#if sitting == null}
      <span class="font-semibold">{m.record_governments()}</span>
      <span class="text-muted-foreground">{m.record_governments_key()}</span>
    {:else}
      <span class="flex items-center gap-1.5 font-semibold">
        <span
          class="rounded-xs block-2.5 inline-2.5"
          style:background={tint(sitting.party)}
        ></span>
        {sitting.name}
      </span>
      {#if sitting.head != null}
        <span
          >{m.record_governments_head({ name: "" })}<Name
            label={sitting.head.name}
            person={sitting.head.id}
          /></span
        >
      {/if}
      <span class="font-figures text-xs text-muted-foreground tabular-nums"
        >{date(sitting.from)} – {sitting.to == null
          ? m.governments_present()
          : date(sitting.to)}</span
      >
    {/if}
  </p>
{/if}
<ul class="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
  {#each legend as party (party ?? "none")}
    <li class="flex items-center gap-1.5">
      <span
        class="rounded-xs block-2.5 inline-2.5"
        style:background={tint(party)}
        style:background-image={info.get(party ?? "")?.isStriped === true
          ? "repeating-linear-gradient(45deg, transparent 0 2px, var(--card) 2px 3px)"
          : null}
      ></span>
      {#if party == null || info.get(party)?.isMade === true}
        <span class="text-muted-foreground">{label(party, shown)}</span>
      {:else}
        <Name
          label={label(party, shown)}
          {party}
          color={tint(party)}
          hasSwatch={false}
        />
      {/if}
    </li>
  {/each}
  {#if terms.some((term) => term.seceded > 0)}
    <li class="flex items-center gap-1.5">
      <span
        class="rounded-xs border border-dashed border-muted-foreground bg-muted block-2.5 inline-2.5"
      ></span>
      <span class="text-muted-foreground">{m.seats_seceded()}</span>
    </li>
  {/if}
</ul>
