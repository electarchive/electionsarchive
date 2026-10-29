<script lang="ts">
  import { resolve } from "$app/paths";
  import Export from "#lib/components/export.svelte";
  import Measured from "#lib/components/measured.svelte";
  import Name from "#lib/components/name.svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { RELATIONS, named as years } from "#lib/relations.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    party,
    color,
    links,
  }: {
    party: PageData["party"];
    color: string;
    links: Array<{ text: string; party: { id: string; color: string | null } }>;
  } = $props();

  const INTO = new Set([
    "merged_into:in",
    "absorbed_by:in",
    "refounded_as:in",
    "successor_of:out",
    "split_from:out",
  ]);
  const LINE = 14;
  const GAP = 10;
  const KEYS: Array<[string, () => string]> = [
    ["merged_into", m.lineage_key_merger],
    ["absorbed_by", m.lineage_key_absorption],
    ["split_from", m.lineage_key_split],
    ["successor_of", m.lineage_key_succession],
    ["list_vehicle_for", m.lineage_key_list],
    ["closed_by_court", m.rel_closed_by_court],
    ["closed_by_government", m.rel_closed_by_government],
    ["renamed", m.lineage_key_rename],
  ];
  const DASHES: Record<string, string> = {
    successor_of: "4 8",
    refounded_as: "4 8",
    list_vehicle_for: "0.1 6",
  };

  const uid = $props.id();
  let width = $state(0);
  let lit = $state<string | null>(null);
  let tip = $state<{
    key: string;
    x: number;
    y: number;
    title: string;
    short: string | null;
    detail: string;
  } | null>(null);
  let chart = $state<SVGSVGElement | null>(null);

  const year = (value: string) =>
    Number(value.slice(0, 4)) + (Number(value.slice(5, 7) || "7") - 1) / 12;
  const measure = $derived.by(() => {
    const context =
      chart == null ? null : document.createElement("canvas").getContext("2d");
    const family = chart == null ? "" : getComputedStyle(chart).fontFamily;
    return (text: string, size: number, weight: number) => {
      if (context == null) {
        return text.length * size * 0.56;
      }
      context.font = `${weight} ${size}px ${family}`;
      return context.measureText(text).width;
    };
  });

  const title = $derived(party.short ?? party.name);
  const names = $derived(party.names ?? []);
  const isRenamed = $derived(names.length > 1);
  const hasChart = $derived(
    isRenamed || (party.events ?? []).length > 0 || party.relations.length > 0,
  );
  const self = $derived({
    from: party.span == null ? null : year(party.span.from),
    to: party.span == null ? null : year(party.span.to),
  });
  const others = $derived(
    Map.groupBy(party.relations, (relation) => relation.party.id)
      .values()
      .toArray()
      .flatMap((group) => {
        const first = group[0];
        if (first == null) {
          return [];
        }
        const notes = group.map((relation) => {
          const text =
            RELATIONS[relation.kind]?.[relation.outgoing ? 1 : 0]?.({
              name: title,
            }) ?? "";
          const at = relation.date?.slice(0, 4) ?? null;
          return {
            text,
            at,
            label: [text, at].filter((part) => part != null).join(" "),
          };
        });
        const events = group.map((relation, index) => ({
          kind: relation.kind,
          at: relation.date == null ? null : year(relation.date),
          isInto: INTO.has(
            `${relation.kind}:${relation.outgoing ? "out" : "in"}`,
          ),
          label: notes[index]?.label ?? "",
        }));
        return [
          {
            party: first.party,
            span: first.span,
            events,
            isAbove: events.every((event) => event.isInto),
            notes,
          },
        ];
      }),
  );
  const main = $derived.by(() => {
    let from = self.from ?? Infinity;
    let to = self.to ?? -Infinity;
    for (const other of others) {
      for (const event of other.events) {
        if (event.at != null) {
          from = Math.min(from, event.at);
          to = Math.max(to, event.at);
        }
      }
    }
    for (const event of party.events ?? []) {
      if (event.date != null) {
        from = Math.min(from, year(event.date));
        to = Math.max(to, year(event.date));
      }
    }
    if (isRenamed) {
      for (const entry of names) {
        for (const at of [entry.from, entry.to]) {
          if (at != null) {
            from = Math.min(from, year(at));
            to = Math.max(to, year(at));
          }
        }
      }
    }
    return { from, to };
  });
  const placed = $derived.by(() => {
    const spans = others.map((other) => {
      const dates = other.events.flatMap((event) =>
        event.at == null ? [] : [event.at],
      );
      const anchor =
        dates.length > 0
          ? other.isAbove
            ? Math.max(...dates)
            : Math.min(...dates)
          : other.isAbove
            ? main.from
            : main.to;
      const from =
        other.span == null
          ? Math.min(...dates, other.isAbove ? anchor - 4 : anchor)
          : Math.min(year(other.span.from), ...dates);
      const to =
        other.span == null
          ? Math.max(...dates, other.isAbove ? anchor : anchor + 4)
          : Math.max(year(other.span.to), ...dates);
      return { ...other, anchor, from, to };
    });
    const above = spans
      .filter((entry) => entry.isAbove)
      .toSorted((a, b) => a.to - b.to || a.anchor - b.anchor);
    const below = spans
      .filter((entry) => !entry.isAbove)
      .toSorted((a, b) => b.from - a.from || b.anchor - a.anchor);
    const rows: Array<(typeof spans)[number] & { offset: number }> = [];
    let cursor = -GAP - 8;
    for (const entry of above) {
      const tall = 18 + LINE * entry.notes.length;
      rows.push({ ...entry, offset: cursor - tall + 9 });
      cursor -= tall + GAP;
    }
    const top = cursor + GAP;
    cursor = GAP + 8 + (isRenamed ? 14 : 0);
    for (const entry of below) {
      rows.push({ ...entry, offset: cursor + 9 });
      cursor += 18 + LINE * entry.notes.length + GAP;
    }
    return { rows, top, bottom: cursor - GAP };
  });
  const gutter = $derived(
    Math.min(
      240,
      Math.max(96, width * 0.36),
      Math.max(
        measure(title, 13, 700),
        ...placed.rows.flatMap((lane) => [
          measure(lane.party.label, 13, 500),
          ...lane.notes.map((note) => measure(note.label, 11, 400)),
        ]),
      ) + 4,
    ),
  );
  const fit = (
    text: string,
    size: number,
    weight: number,
    tail: string | null = null,
  ) => {
    const whole = tail == null ? text : `${text} ${tail}`;
    if (measure(whole, size, weight) <= gutter) {
      return whole;
    }
    const ending = tail == null ? "…" : `… ${tail}`;
    let cut = text.length;
    while (
      cut > 1 &&
      measure(`${text.slice(0, cut)}${ending}`, size, weight) > gutter
    ) {
      cut -= 1;
    }
    return `${text.slice(0, cut).trimEnd()}${ending}`;
  };
  const lanes = $derived(
    placed.rows.map((lane) => {
      const name =
        lane.party.short != null && measure(lane.party.label, 13, 500) > gutter
          ? lane.party.short
          : lane.party.label;
      return {
        ...lane,
        name: fit(name, 13, 500),
        lines: lane.notes.map((note) => fit(note.text, 11, 400, note.at)),
      };
    }),
  );
  const extent = $derived.by(() => {
    const found: Array<number> = [];
    for (const value of [main.from, main.to]) {
      if (Number.isFinite(value)) {
        found.push(value);
      }
    }
    for (const lane of lanes) {
      found.push(lane.from, lane.to);
    }
    if (found.length === 0) {
      return { low: 2000, high: 2001 };
    }
    const low = Math.min(...found);
    const high = Math.max(...found);
    const pad = Math.max(1, (high - low) * 0.02);
    if (!Number.isFinite(main.from) || !Number.isFinite(main.to)) {
      return { low: low - pad, high: high + pad };
    }
    const room = Math.max(4, (main.to - main.from) * 0.35);
    return {
      low: Math.max(low - pad, main.from - room),
      high: Math.min(high + pad, main.to + room),
    };
  });
  const axis = $derived(4 - placed.top);
  const height = $derived(axis + placed.bottom + 30);
  const start = $derived(gutter + 20);
  const end = $derived(width - 6);
  const bend = $derived(Math.min(56, Math.max(20, (end - start) * 0.05)));
  const sx = (value: number) =>
    start + ((value - extent.low) / (extent.high - extent.low)) * (end - start);
  const segments = $derived(
    isRenamed && Number.isFinite(main.from)
      ? names.map((entry, index) => {
          const from = sx(
            entry.from == null
              ? main.from
              : Math.max(main.from, year(entry.from)),
          );
          const to = sx(
            entry.to == null ? main.to : Math.min(main.to, year(entry.to)),
          );
          const room = to - from - 6;
          return {
            entry,
            from,
            to,
            isFormer: index < names.length - 1,
            label:
              measure(entry.name, 11, 500) <= room
                ? entry.name
                : entry.short != null && measure(entry.short, 11, 500) <= room
                  ? entry.short
                  : null,
          };
        })
      : [],
  );
  const closures = $derived(
    (party.events ?? []).flatMap((event) =>
      event.date == null || !Number.isFinite(main.from)
        ? []
        : [{ ...event, x: sx(year(event.date)) }],
    ),
  );
  const kinds = $derived(
    new Set([
      ...lanes.flatMap((lane) =>
        lane.events.flatMap((event) =>
          event.at == null
            ? []
            : [event.kind === "refounded_as" ? "successor_of" : event.kind],
        ),
      ),
      ...closures.map((event) => event.kind),
      ...(segments.length > 0 ? ["renamed"] : []),
    ]),
  );
  const hint = (next: NonNullable<typeof tip>) => ({
    role: "button",
    tabindex: 0,
    "aria-label": `${next.title}${next.short == null ? "" : ` (${next.short})`}, ${next.detail}`,
    onpointerenter: (event: PointerEvent) => {
      if (event.pointerType === "mouse") {
        tip = next;
      }
    },
    onpointerleave: (event: PointerEvent) => {
      if (event.pointerType === "mouse" && tip?.key === next.key) {
        tip = null;
      }
    },
    onfocus: () => (tip = next),
    onblur: () => {
      if (tip?.key === next.key) {
        tip = null;
      }
    },
    onclick: () => (tip = next),
    onkeydown: (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        tip = null;
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        tip = next;
      }
    },
  });
  const ticks = $derived.by(() => {
    const span = extent.high - extent.low;
    const room = Math.max(1, Math.floor((end - start) / 56));
    const step =
      [1, 2, 5, 10, 20, 25, 50, 100].find((value) => span / value <= room) ??
      100;
    const found: Array<number> = [];
    for (
      let tick = Math.ceil(extent.low / step) * step;
      tick <= extent.high;
      tick += step
    ) {
      found.push(tick);
    }
    return found;
  });
</script>

{#if hasChart || links.length > 0}
  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="lineage-title"
    data-export
  >
    {#if hasChart}
      <Export name="lineage" class="absolute inset-e-2 inset-bs-2" />
    {/if}
    <div class="grid gap-0.5 pe-8">
      <h2 id="lineage-title" class="heading">
        {party.parents.length > 0
          ? m.party_relations()
          : m.party_lineage_title()}
      </h2>
      {#if hasChart}
        <p
          class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
        >
          {#if lanes.length > 0}
            {m.party_lineage_lede()}
          {/if}
          {m.party_lineage_hint()}
        </p>
      {/if}
    </div>
    {#if hasChart}
      <Measured bind:width class="relative pbs-1 min-inline-0">
        <svg
          bind:this={chart}
          class="block overflow-visible block-auto max-inline-full"
          viewBox="0 0 {width} {height}"
          {width}
          {height}
          role="group"
          aria-labelledby="lineage-title"
          aria-describedby="lineage-list"
        >
          {#each ticks as tick (tick)}
            <line
              x1={sx(tick)}
              x2={sx(tick)}
              y1={0}
              y2={height - 22}
              class="stroke-border/60"
            />
            <text
              x={sx(tick)}
              y={height - 6}
              text-anchor="middle"
              class="fill-muted-foreground font-figures text-[0.6875rem] tabular-nums"
              >{tick}</text
            >
          {/each}
          {#each lanes as lane (lane.party.id)}
            {const tint = $derived(
              swatch(lane.party.color, `party:${lane.party.id}`),
            )}
            {const y = $derived(axis + lane.offset)}
            {const marks = $derived(
              lane.events
                .flatMap((event) =>
                  event.at == null ? [] : [{ ...event, x: sx(event.at) }],
                )
                .toSorted((a, b) => a.x - b.x),
            )}
            {const turn = $derived(
              Math.min(
                bend,
                ...marks
                  .slice(1)
                  .map((mark, index) =>
                    Math.max(8, (mark.x - (marks[index]?.x ?? 0)) / 2 - 2),
                  ),
              ),
            )}
            {const isCutStart = $derived(lane.from < extent.low)}
            {const isCutEnd = $derived(lane.to > extent.high)}
            {const span = $derived.by(() => {
              let from = sx(Math.max(lane.from, extent.low));
              let to = sx(Math.min(lane.to, extent.high));
              for (const { x, isInto } of marks) {
                if (isInto && to <= x + 1) {
                  to = Math.max(from, x - turn);
                }
                if (!isInto && from >= x - 1) {
                  from = Math.min(to, x + turn);
                }
              }
              return { from, to };
            })}
            {const paths = $derived([
              ...marks.map(({ x, isInto, kind }) => ({
                d: isInto
                  ? `M${x - turn},${y} C${x - turn / 2},${y} ${x - turn / 2},${axis} ${x},${axis}`
                  : `M${x},${axis} C${x + turn / 2},${axis} ${x + turn / 2},${y} ${x + turn},${y}`,
                dash: DASHES[kind],
              })),
              ...(span.to - span.from > 0.5
                ? [{ d: `M${span.from},${y} H${span.to}`, dash: undefined }]
                : []),
            ])}
            {const reach = $derived(measure(lane.name, 13, 500) + 8)}
            {const left = $derived(
              Math.min(
                span.to - span.from > 0.5 ? span.from : Infinity,
                ...marks.map(({ x, isInto }) => (isInto ? x - turn : x + turn)),
              ),
            )}
            <g
              role="presentation"
              class={cn("transition-opacity duration-200", {
                "opacity-20": lit != null && lit !== lane.party.id,
              })}
              onpointerenter={() => (lit = lane.party.id)}
              onpointerleave={() => (lit = null)}
            >
              {#if left - reach > 12}
                <line
                  x1={reach}
                  x2={left - 6}
                  y1={y}
                  y2={y}
                  class="stroke-muted-foreground/45"
                  stroke-dasharray="1 4"
                  stroke-linecap="round"
                />
              {/if}
              {#each paths as line, index (index)}
                <path
                  d={line.d}
                  fill="none"
                  class="stroke-card"
                  stroke-width="9"
                  stroke-linecap="round"
                />
              {/each}
              {#each paths as line, index (index)}
                <path
                  d={line.d}
                  fill="none"
                  stroke={tint}
                  stroke-width="4"
                  stroke-linecap="round"
                  stroke-dasharray={line.dash}
                />
              {/each}
              {#if isCutStart && span.to - span.from > 0.5}
                <path
                  d="M{span.from + 5},{y - 5} L{span.from},{y} L{span.from +
                    5},{y + 5}"
                  fill="none"
                  class="stroke-card"
                  stroke-width="5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
                <path
                  d="M{span.from + 5},{y - 5} L{span.from},{y} L{span.from +
                    5},{y + 5}"
                  fill="none"
                  class="stroke-foreground"
                  stroke-width="1.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
                <text
                  x={span.from + 9}
                  y={y - 6}
                  class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
                  >{Math.floor(lane.from)}</text
                >
              {/if}
              {#if isCutEnd && span.to - span.from > 0.5}
                <path
                  d="M{span.to - 5},{y - 5} L{span.to},{y} L{span.to - 5},{y +
                    5}"
                  fill="none"
                  class="stroke-card"
                  stroke-width="5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
                <path
                  d="M{span.to - 5},{y - 5} L{span.to},{y} L{span.to - 5},{y +
                    5}"
                  fill="none"
                  class="stroke-foreground"
                  stroke-width="1.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
                <text
                  x={span.to - 9}
                  y={y - 6}
                  text-anchor="end"
                  class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
                  >{Math.floor(lane.to)}</text
                >
              {/if}
              <a href={resolve("/id/[id]", { id: lane.party.id })}>
                <text
                  x="0"
                  y={y + 4.5}
                  class="fill-foreground text-[0.8125rem] font-medium hover:underline"
                  >{lane.name}<title>{lane.party.label}</title></text
                >
              </a>
              {#each lane.lines as line, index (index)}
                <text
                  x="0"
                  y={y + 4.5 + LINE * (index + 1)}
                  class="fill-muted-foreground text-[0.6875rem] tabular-nums"
                  >{line}</text
                >
              {/each}
            </g>
          {/each}
          {#if Number.isFinite(main.from)}
            <g
              class={cn("transition-opacity duration-200", {
                "opacity-40": lit != null,
              })}
            >
              {#if sx(main.from) - measure(title, 13, 700) > 20}
                <line
                  x1={measure(title, 13, 700) + 8}
                  x2={sx(main.from) - 8}
                  y1={axis}
                  y2={axis}
                  class="stroke-muted-foreground/45"
                  stroke-dasharray="1 4"
                  stroke-linecap="round"
                />
              {/if}
              {#if segments.length > 0}
                <clipPath id="{uid}-bar">
                  <rect
                    x={sx(main.from)}
                    y={axis - 5}
                    width={Math.max(10, sx(main.to) - sx(main.from))}
                    height="10"
                    rx="5"
                  />
                </clipPath>
                <g clip-path="url(#{uid}-bar)">
                  {#each segments as segment, index (index)}
                    <rect
                      x={segment.from}
                      y={axis - 5}
                      width={Math.max(0, segment.to - segment.from)}
                      height="10"
                      fill={color}
                      fill-opacity={segment.isFormer ? 0.5 : 1}
                    />
                  {/each}
                </g>
                {#each segments.slice(1) as segment, index (index)}
                  <line
                    x1={segment.from}
                    x2={segment.from}
                    y1={axis - 6}
                    y2={axis + 6}
                    class="stroke-card"
                    stroke-width="2.5"
                  />
                  <line
                    x1={segment.from}
                    x2={segment.from}
                    y1={axis - 9}
                    y2={axis + 9}
                    class="stroke-foreground"
                    stroke-width="1"
                    stroke-dasharray="3 12"
                  />
                {/each}
                {#each segments as segment, index (index)}
                  {#if segment.label != null}
                    <text
                      x={segment.from + 3}
                      y={axis + 19}
                      class={cn(
                        "fill-muted-foreground stroke-card text-[0.6875rem] font-medium [paint-order:stroke]",
                        { "fill-foreground": tip?.key === `name:${index}` },
                      )}
                      stroke-width="3">{segment.label}</text
                    >
                  {/if}
                {/each}
              {:else}
                <rect
                  x={sx(main.from)}
                  y={axis - 5}
                  width={Math.max(10, sx(main.to) - sx(main.from))}
                  height="10"
                  rx="5"
                  fill={color}
                />
              {/if}
              <text
                x="0"
                y={axis + 4.5}
                class="fill-foreground text-[0.8125rem] font-bold"
                >{fit(title, 13, 700)}</text
              >
            </g>
          {/if}
          {#each lanes as lane (lane.party.id)}
            {const tint = $derived(
              swatch(lane.party.color, `party:${lane.party.id}`),
            )}
            {#each lane.events as event, index (index)}
              {#if event.at != null}
                {const x = $derived(sx(event.at))}
                <g
                  class={cn(
                    "group cursor-default transition-opacity duration-200 outline-none",
                    { "opacity-20": lit != null && lit !== lane.party.id },
                  )}
                  {...hint({
                    key: `rel:${lane.party.id}:${index}`,
                    x,
                    y: axis + 14,
                    title: lane.party.label,
                    short: null,
                    detail: event.label,
                  })}
                >
                  <circle
                    cx={x}
                    cy={axis}
                    r="10"
                    fill="transparent"
                    class="stroke-transparent group-focus-visible:stroke-ring"
                    stroke-width="2"
                  />
                  {#if event.kind === "absorbed_by"}
                    <rect
                      x={x - 4.25}
                      y={axis - 4.25}
                      width="8.5"
                      height="8.5"
                      rx="1"
                      fill={tint}
                      class="stroke-card"
                      stroke-width="2"
                    />
                  {:else if event.kind === "split_from"}
                    <path
                      d="M{x},{axis - 6} L{x + 6},{axis} L{x},{axis + 6} L{x -
                        6},{axis} Z"
                      fill={tint}
                      class="stroke-card"
                      stroke-width="2"
                      stroke-linejoin="round"
                    />
                  {:else if event.kind === "successor_of" || event.kind === "refounded_as"}
                    <circle
                      cx={x}
                      cy={axis}
                      r="4.5"
                      class="fill-card"
                      stroke={tint}
                      stroke-width="2.5"
                    />
                  {:else if event.kind === "list_vehicle_for"}
                    <rect
                      x={x - 4}
                      y={axis - 4}
                      width="8"
                      height="8"
                      rx="1"
                      class="fill-card"
                      stroke={tint}
                      stroke-width="2"
                    />
                  {:else}
                    <circle
                      cx={x}
                      cy={axis}
                      r="4.5"
                      fill={tint}
                      class="stroke-card"
                      stroke-width="2"
                    />
                  {/if}
                </g>
              {/if}
            {/each}
          {/each}
          {#each closures as event, index (index)}
            <g
              class="group cursor-default outline-none"
              {...hint({
                key: `closed:${index}`,
                x: event.x,
                y: axis + 14,
                title: (event.kind === "closed_by_government"
                  ? m.rel_closed_by_government
                  : m.rel_closed_by_court)(),
                short: null,
                detail: event.date?.slice(0, 4) ?? "",
              })}
            >
              {const top = $derived(axis - 15)}
              <circle
                cx={event.x}
                cy={top}
                r="9"
                fill="transparent"
                class="stroke-transparent group-focus-visible:stroke-ring"
                stroke-width="2"
              />
              <line
                x1={event.x}
                x2={event.x}
                y1={top + 5}
                y2={axis - 5}
                class="stroke-foreground"
                stroke-width="1"
              />
              <path
                d="M{event.x - 4},{top - 4} L{event.x + 4},{top + 4} M{event.x +
                  4},{top - 4} L{event.x - 4},{top + 4}"
                class="stroke-card"
                stroke-width="5"
                stroke-linecap="round"
              />
              <path
                d="M{event.x - 4},{top - 4} L{event.x + 4},{top + 4} M{event.x +
                  4},{top - 4} L{event.x - 4},{top + 4}"
                class="stroke-foreground"
                stroke-width="1.75"
                stroke-linecap="round"
              />
            </g>
          {/each}
          {#each segments as segment, index (index)}
            <rect
              x={segment.from}
              y={axis - 9}
              width={Math.max(8, segment.to - segment.from)}
              height="32"
              fill="transparent"
              class="cursor-default outline-none focus-visible:stroke-ring"
              stroke-width="2"
              {...hint({
                key: `name:${index}`,
                x: (segment.from + segment.to) / 2,
                y: axis + 28,
                title: segment.entry.name,
                short: segment.entry.short,
                detail: years(segment.entry),
              })}
            />
          {/each}
        </svg>
        {#if tip != null}
          <div
            class="pointer-events-none absolute grid gap-0.5 rounded-sm bg-card/95 px-2.5 py-1.5 text-xs shadow-menu ring-1 ring-border max-inline-60"
            style:inset-inline-start="{Math.max(
              0,
              Math.min(width - 240, tip.x - 60),
            )}px"
            style:inset-block-start="{tip.y}px"
          >
            <span class="text-[0.8125rem] font-semibold"
              >{tip.title}{#if tip.short != null}
                <span class="ms-1.5 font-normal text-muted-foreground"
                  >{tip.short}</span
                >{/if}</span
            >
            <span class="font-figures text-muted-foreground tabular-nums"
              >{tip.detail.charAt(0).toUpperCase()}{tip.detail.slice(1)}</span
            >
          </div>
        {/if}
        <ul id="lineage-list" class="sr-only">
          {#each segments as segment, index (index)}
            <li>{segment.entry.name}: {years(segment.entry)}</li>
          {/each}
          {#each closures as event, index (index)}
            <li>
              {(event.kind === "closed_by_government"
                ? m.rel_closed_by_government
                : m.rel_closed_by_court)()}: {event.date?.slice(0, 4) ?? ""}
            </li>
          {/each}
          {#each lanes as lane (lane.party.id)}
            <li>
              {lane.party.label}: {lane.notes
                .map((note) => note.label)
                .join(", ")}
            </li>
          {/each}
        </ul>
      </Measured>
      {#if kinds.size > 0}
        <ul
          class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
        >
          {#each KEYS.filter( ([kind]) => kinds.has(kind) ) as [kind, label] (kind)}
            <li class="flex items-center gap-1.5">
              <svg
                viewBox="-8 -8 16 16"
                class="shrink-0 overflow-visible block-3.5 inline-3.5"
                aria-hidden="true"
              >
                {#if kind === "absorbed_by"}
                  <rect
                    x="-4.25"
                    y="-4.25"
                    width="8.5"
                    height="8.5"
                    rx="1"
                    class="fill-muted-foreground"
                  />
                {:else if kind === "split_from"}
                  <path
                    d="M0,-6 L6,0 L0,6 L-6,0 Z"
                    class="fill-muted-foreground"
                  />
                {:else if kind === "successor_of"}
                  <circle
                    r="4.5"
                    class="fill-card stroke-muted-foreground"
                    stroke-width="2.5"
                  />
                {:else if kind === "list_vehicle_for"}
                  <rect
                    x="-4"
                    y="-4"
                    width="8"
                    height="8"
                    rx="1"
                    class="fill-card stroke-muted-foreground"
                    stroke-width="2"
                  />
                {:else if kind === "closed_by_court" || kind === "closed_by_government"}
                  <path
                    d="M-4,-4 L4,4 M4,-4 L-4,4"
                    class="stroke-foreground"
                    stroke-width="1.75"
                    stroke-linecap="round"
                  />
                {:else if kind === "renamed"}
                  <rect
                    x="-8"
                    y="-3"
                    width="16"
                    height="6"
                    class="fill-muted-foreground"
                  />
                  <line
                    x1="0"
                    x2="0"
                    y1="-4"
                    y2="4"
                    class="stroke-card"
                    stroke-width="2.5"
                  />
                  <line
                    x1="0"
                    x2="0"
                    y1="-7"
                    y2="7"
                    class="stroke-foreground"
                    stroke-dasharray="3 8"
                  />
                {:else}
                  <circle r="4.5" class="fill-muted-foreground" />
                {/if}
              </svg>
              {label()}
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
    {#if links.length > 0}
      <ul class="grid gap-x-6 gap-y-1 text-sm md:grid-cols-2">
        {#each links as link, index (index)}
          <li class="flex items-baseline gap-2">
            <span
              class="shrink-0 self-center rounded-xs block-2 inline-2"
              style:background-color={swatch(
                link.party.color,
                `party:${link.party.id}`,
              )}
            ></span>
            <Name
              label={link.text}
              party={link.party.id}
              color={swatch(link.party.color, `party:${link.party.id}`)}
              hasSwatch={false}
            />
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}
