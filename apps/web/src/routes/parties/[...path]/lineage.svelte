<script lang="ts">
  import { resolve } from "$app/paths";
  import Export from "#lib/components/export.svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";

  const { party, color }: { party: PageData["party"]; color: string } =
    $props();

  const INTO = new Set([
    "merged_into:in",
    "absorbed_by:in",
    "refounded_as:in",
    "successor_of:out",
    "split_from:out",
  ]);
  const ROW = 30;
  const PAD = { top: 8, bottom: 24, side: 14 };

  let width = $state(720);

  const year = (value: string) =>
    Number(value.slice(0, 4)) + (Number(value.slice(5, 7) || "7") - 1) / 12;

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
      .flatMap((group) =>
        group[0] == null
          ? []
          : [
              {
                party: group[0].party,
                span: group[0].span,
                events: group.map((relation) => ({
                  at: relation.date == null ? null : year(relation.date),
                  isInto: INTO.has(
                    `${relation.kind}:${relation.outgoing ? "out" : "in"}`,
                  ),
                })),
              },
            ],
      ),
  );
  const extent = $derived.by(() => {
    const found: Array<number> = [];
    for (const value of [self.from, self.to]) {
      if (value != null) {
        found.push(value);
      }
    }
    for (const other of others) {
      for (const event of other.events) {
        if (event.at != null) {
          found.push(event.at);
        }
      }
      if (other.span != null) {
        found.push(year(other.span.from), year(other.span.to));
      }
    }
    if (found.length === 0) {
      return { low: 2000, high: 2001 };
    }
    const low = Math.min(...found);
    const high = Math.max(...found);
    const pad = Math.max(2, (high - low) * 0.04);
    return { low: low - pad, high: high + pad };
  });
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
  const rows = $derived.by(() => {
    const placed = others.map((other) => {
      const dates = other.events.flatMap((event) =>
        event.at == null ? [] : [event.at],
      );
      const isAbove = other.events.every((event) => event.isInto);
      const fallback = isAbove ? main.from : main.to;
      const from = Math.min(
        ...(other.span == null ? [] : [year(other.span.from)]),
        ...dates,
        ...(dates.length === 0 && other.span == null ? [fallback - 4] : []),
      );
      const to = Math.max(
        ...(other.span == null ? [] : [year(other.span.to)]),
        ...dates,
        ...(dates.length === 0 && other.span == null ? [fallback + 4] : []),
      );
      return { ...other, isAbove, from, to: Math.max(to, from + 0.6) };
    });
    const above = placed
      .filter((entry) => entry.isAbove)
      .toSorted((a, b) => b.to - a.to);
    const below = placed
      .filter((entry) => !entry.isAbove)
      .toSorted((a, b) => a.from - b.from);
    return [
      ...above.map((entry, index) => ({ ...entry, row: above.length - index })),
      ...below.map((entry, index) => ({ ...entry, row: -(index + 1) })),
    ];
  });
  const lift = $derived(
    Math.max(0, ...rows.filter((r) => r.isAbove).map((r) => r.row)),
  );
  const drop = $derived(rows.filter((r) => !r.isAbove).length);
  const height = $derived(PAD.top + (lift + drop + 1) * ROW + PAD.bottom);
  const sx = (value: number) =>
    PAD.side +
    ((value - extent.low) / (extent.high - extent.low)) *
      (width - PAD.side * 2);
  const sy = (row: number) => PAD.top + (lift - row) * ROW + ROW / 2;
  const ticks = $derived.by(() => {
    const span = extent.high - extent.low;
    const step = span > 60 ? 20 : span > 30 ? 10 : span > 12 ? 5 : 2;
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

{#if rows.length > 0}
  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="lineage-title"
    data-export
  >
    <Export name="lineage" class="absolute inset-e-2 inset-bs-2" />
    <div class="grid gap-0.5 pe-8">
      <h2
        id="lineage-title"
        class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
      >
        {m.party_lineage_title()}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
        {m.party_lineage_lede()}
      </p>
    </div>
    <div class="min-inline-0" bind:clientWidth={width}>
      <svg
        class="block overflow-visible block-auto max-inline-full"
        viewBox="0 0 {width} {height}"
        {width}
        {height}
        role="img"
        aria-label={m.party_lineage_title()}
      >
        {#each ticks as tick (tick)}
          <line
            x1={sx(tick)}
            x2={sx(tick)}
            y1={PAD.top}
            y2={height - PAD.bottom}
            class="stroke-border"
            stroke-dasharray="2 3"
          />
          <text
            x={sx(tick)}
            y={height - 6}
            text-anchor="middle"
            class="fill-muted-foreground font-mono text-[0.625rem] tabular-nums"
            >{tick}</text
          >
        {/each}
        {#each rows as entry (entry.party.id)}
          {const tint = $derived(
            swatch(entry.party.color, `party:${entry.party.id}`),
          )}
          {#each entry.events as event, index (index)}
            {#if event.at != null}
              {const x = $derived(sx(event.at))}
              {const y0 = $derived(sy(entry.row))}
              {const y1 = $derived(sy(0))}
              <path
                d={event.isInto
                  ? `M${x - 18},${y0} Q${x},${y0} ${x},${(y0 + y1) / 2} L${x},${y1}`
                  : `M${x},${y1} L${x},${(y0 + y1) / 2} Q${x},${y0} ${x + 18},${y0}`}
                fill="none"
                stroke={tint}
                stroke-width="2"
                stroke-opacity="0.85"
              />
              <circle cx={x} cy={y1} r="3.5" fill={tint} class="stroke-card" />
            {/if}
          {/each}
          <rect
            x={sx(entry.from)}
            y={sy(entry.row) - 3}
            width={Math.max(4, sx(entry.to) - sx(entry.from))}
            height="6"
            rx="3"
            fill={tint}
          />
        {/each}
        {#if Number.isFinite(main.from)}
          <rect
            x={sx(main.from)}
            y={sy(0) - 5}
            width={Math.max(6, sx(main.to) - sx(main.from))}
            height="10"
            rx="5"
            fill={color}
          />
          <text
            x={Math.min(sx(main.from), width - 140)}
            y={sy(0) - 10}
            class="fill-foreground stroke-card text-[0.8125rem] font-bold"
            stroke-width="4"
            stroke-linejoin="round"
            paint-order="stroke">{party.short ?? party.name}</text
          >
        {/if}
        {#each rows as entry (entry.party.id)}
          <a href={resolve("/id/[id]", { id: entry.party.id })}>
            <text
              x={Math.min(sx(entry.from), width - 120)}
              y={sy(entry.row) - 7}
              class="fill-foreground stroke-card text-[0.75rem] font-semibold hover:underline"
              stroke-width="4"
              stroke-linejoin="round"
              paint-order="stroke"
              >{entry.party.short ?? entry.party.label}</text
            >
          </a>
        {/each}
      </svg>
    </div>
  </section>
{/if}
