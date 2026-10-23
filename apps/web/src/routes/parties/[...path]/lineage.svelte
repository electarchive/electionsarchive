<script lang="ts">
  import { resolve } from "$app/paths";
  import Export from "#lib/components/export.svelte";
  import Measured from "#lib/components/measured.svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { RELATIONS } from "#lib/relations.js";
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

  let width = $state(0);
  let lit = $state<string | null>(null);
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
  const self = $derived({
    from: party.span == null ? null : year(party.span.from),
    to: party.span == null ? null : year(party.span.to),
  });
  const others = $derived(
    Map.groupBy(
      party.relations.filter(
        (relation) => relation.kind !== "list_vehicle_for",
      ),
      (relation) => relation.party.id,
    )
      .values()
      .toArray()
      .flatMap((group) => {
        const first = group[0];
        if (first == null) {
          return [];
        }
        const events = group.map((relation) => ({
          at: relation.date == null ? null : year(relation.date),
          isInto: INTO.has(
            `${relation.kind}:${relation.outgoing ? "out" : "in"}`,
          ),
        }));
        return [
          {
            party: first.party,
            span: first.span,
            events,
            isAbove: events.every((event) => event.isInto),
            notes: group.map((relation) => {
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
            }),
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
    cursor = GAP + 8;
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
    return { low: low - pad, high: high + pad };
  });
  const axis = $derived(4 - placed.top);
  const height = $derived(axis + placed.bottom + 30);
  const start = $derived(gutter + 20);
  const end = $derived(width - 6);
  const bend = $derived(Math.min(56, Math.max(20, (end - start) * 0.05)));
  const sx = (value: number) =>
    start + ((value - extent.low) / (extent.high - extent.low)) * (end - start);
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

{#if lanes.length > 0 || links.length > 0}
  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="lineage-title"
    data-export
  >
    {#if lanes.length > 0}
      <Export name="lineage" class="absolute inset-e-2 inset-bs-2" />
    {/if}
    <div class="grid gap-0.5 pe-8">
      <h2 id="lineage-title" class="heading">
        {party.parents.length > 0
          ? m.party_relations()
          : m.party_lineage_title()}
      </h2>
      {#if lanes.length > 0}
        <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
          {m.party_lineage_lede()}
        </p>
      {/if}
    </div>
    {#if lanes.length > 0}
      <Measured bind:width class="pbs-1 min-inline-0">
        <svg
          bind:this={chart}
          class="block overflow-visible block-auto max-inline-full"
          viewBox="0 0 {width} {height}"
          {width}
          {height}
          role="img"
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
            {const span = $derived.by(() => {
              let from = sx(lane.from);
              let to = sx(lane.to);
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
            {const path = $derived(
              [
                ...marks.map(({ x, isInto }) =>
                  isInto
                    ? `M${x - turn},${y} C${x - turn / 2},${y} ${x - turn / 2},${axis} ${x},${axis}`
                    : `M${x},${axis} C${x + turn / 2},${axis} ${x + turn / 2},${y} ${x + turn},${y}`,
                ),
                span.to - span.from > 0.5
                  ? `M${span.from},${y} H${span.to}`
                  : "",
              ].join(" "),
            )}
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
              <path
                d={path}
                fill="none"
                class="stroke-card"
                stroke-width="9"
                stroke-linecap="round"
              />
              <path
                d={path}
                fill="none"
                stroke={tint}
                stroke-width="4"
                stroke-linecap="round"
              />
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
              <rect
                x={sx(main.from)}
                y={axis - 5}
                width={Math.max(10, sx(main.to) - sx(main.from))}
                height="10"
                rx="5"
                fill={color}
              />
              <text
                x="0"
                y={axis + 4.5}
                class="fill-foreground text-[0.8125rem] font-bold"
                >{fit(title, 13, 700)}</text
              >
            </g>
          {/if}
          {#each lanes as lane (lane.party.id)}
            {#each lane.events as event, index (index)}
              {#if event.at != null}
                <circle
                  cx={sx(event.at)}
                  cy={axis}
                  r="4.5"
                  fill={swatch(lane.party.color, `party:${lane.party.id}`)}
                  class={cn("stroke-card transition-opacity duration-200", {
                    "opacity-20": lit != null && lit !== lane.party.id,
                  })}
                  stroke-width="2"
                />
              {/if}
            {/each}
          {/each}
        </svg>
        <ul id="lineage-list" class="sr-only">
          {#each lanes as lane (lane.party.id)}
            <li>
              {lane.party.label}: {lane.notes
                .map((note) => note.label)
                .join(", ")}
            </li>
          {/each}
        </ul>
      </Measured>
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
            <a
              class="hover:highlighter"
              href={resolve("/id/[id]", { id: link.party.id })}>{link.text}</a
            >
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}
