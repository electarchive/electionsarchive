<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import Export from "#lib/components/export.svelte";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const { id, isExpected = false }: { id: string; isExpected?: boolean } =
    $props();

  const uid = $props.id();
  const PAD = { top: 8, bottom: 22, side: 2 };

  const record = createQuery(() =>
    orpc.polities.record.queryOptions({ input: { id } }),
  );

  let chosen = $state<string | null>(null);
  let width = $state(960);
  let hovered = $state<number | null>(null);
  let seated = $state<string | null>(null);

  const series = $derived(
    (record.data?.series ?? []).filter((entry) =>
      entry.points.some((point) =>
        point.parties.some((party) => party.party != null),
      ),
    ),
  );
  const parties = $derived(
    new Map((record.data?.parties ?? []).map((party) => [party.id, party])),
  );
  const current = $derived(
    series.find((entry) => entry.id === chosen) ??
      series.toSorted((a, b) => b.points.length - a.points.length)[0] ??
      null,
  );
  const isSingle = $derived(
    current?.isOffice === true &&
      current.points.every((point) => point.filled === 1),
  );
  const cabinets = $derived(record.data?.governments ?? []);
  const band = $derived(cabinets.length > 0 ? 22 : 0);
  const height = $derived((isSingle ? 96 : 240) + band);
  const step = $derived(
    current == null
      ? 0
      : Math.min(
          72,
          (width - PAD.side * 2) / Math.max(1, current.points.length),
        ),
  );
  const gap = $derived(step > 10 ? 2 : step > 4 ? 1 : 0);
  const inner = $derived(height - PAD.top - PAD.bottom - band);
  const tint = (party: string | null) =>
    party == null
      ? swatch(null, "")
      : swatch(parties.get(party)?.color ?? null, `party:${party}`);
  const lean = (party: string | null) =>
    (party == null ? null : parties.get(party)?.lean) ?? Infinity;
  const columns = $derived(
    (current?.points ?? []).map((point, index) => {
      const ordered = point.parties.toSorted(
        (a, b) => lean(a.party) - lean(b.party) || b.seats - a.seats,
      );
      let below = 0;
      const blocks = ordered.map((entry) => {
        const block = {
          ...entry,
          y: PAD.top + inner * (1 - (below + entry.seats) / point.filled),
          h: (inner * entry.seats) / point.filled,
        };
        below += entry.seats;
        return block;
      });
      return {
        ...point,
        index,
        x: PAD.side + index * step,
        blocks,
        strength:
          point.share == null
            ? 1
            : Math.min(1, Math.max(0.55, (point.share - 0.45) / 0.17)),
      };
    }),
  );
  const ticks = $derived.by(() => {
    const every = Math.max(1, Math.ceil(40 / Math.max(step, 1)));
    return columns.filter(
      (column, index) =>
        index === 0 ||
        (index % every === 0 && columns.length - 1 - index >= every) ||
        index === columns.length - 1,
    );
  });
  const legend = $derived(
    current == null
      ? [...new Set(cabinets.map((cabinet) => cabinet.party))].toSorted(
          (a, b) => lean(a) - lean(b),
        )
      : Map.groupBy(
          current.points.flatMap((point) =>
            point.parties.map((entry) => ({
              party: entry.party,
              share: entry.seats / point.filled,
            })),
          ),
          (entry) => entry.party,
        )
          .entries()
          .map(([party, entries]) => ({
            party,
            best: Math.max(...entries.map((entry) => entry.share)),
          }))
          .filter((entry) => entry.best >= 0.08)
          .toArray()
          .toSorted((a, b) => b.best - a.best)
          .slice(0, 12)
          .map((entry) => entry.party)
          .toSorted((a, b) => lean(a) - lean(b)),
  );
  const spans = $derived.by(() => {
    const today = new Date().toISOString().slice(0, 10);
    return columns.flatMap((column, index) => {
      const start = Date.parse(column.date);
      const end = Date.parse(columns[index + 1]?.date ?? today);
      const room = Math.max(step - gap, 0.6);
      return cabinets.flatMap((cabinet) => {
        const from = Math.max(start, Date.parse(cabinet.from));
        const to = Math.min(end, Date.parse(cabinet.to ?? today));
        if (to <= from || end <= start) {
          return [];
        }
        return [
          {
            ...cabinet,
            key: `${column.index}:${cabinet.from}`,
            x: column.x + gap / 2 + ((from - start) / (end - start)) * room,
            w: Math.max(((to - from) / (end - start)) * room, 0.6),
          },
        ];
      });
    });
  });
  const timeline = $derived.by(() => {
    const today = new Date().toISOString().slice(0, 10);
    const low = Date.parse(cabinets[0]?.from ?? today);
    const high = Date.parse(today);
    const room = width - PAD.side * 2;
    const sx = (value: number) =>
      PAD.side + ((value - low) / Math.max(high - low, 1)) * room;
    const first = new Date(low).getUTCFullYear();
    const last = new Date(high).getUTCFullYear();
    const every =
      [5, 10, 20, 25, 50].find((size) => ((last - first) / size) * 56 < room) ??
      50;
    const years: Array<{ year: number; x: number }> = [];
    for (
      let year = Math.ceil((first + 1) / every) * every;
      year <= last;
      year += every
    ) {
      years.push({ year, x: sx(Date.UTC(year, 0, 1)) });
    }
    const spans = cabinets.map((cabinet) => {
      const x = sx(Date.parse(cabinet.from));
      return {
        ...cabinet,
        x,
        w: Math.max(sx(Date.parse(cabinet.to ?? today)) - x - 0.5, 0.6),
      };
    });
    const runs: Array<{
      from: string;
      x: number;
      w: number;
      label: string;
      party: string | null;
    }> = [];
    for (const span of spans) {
      const run = runs.at(-1);
      const label = span.head?.split(" ").at(-1) ?? "";
      if (run?.label === label && run.party === span.party) {
        run.w = span.x + span.w - run.x;
      } else {
        runs.push({
          from: span.from,
          x: span.x,
          w: span.w,
          label,
          party: span.party,
        });
      }
    }
    return {
      years,
      spans,
      runs: runs.filter(
        (run) => run.label !== "" && run.w > run.label.length * 6.4 + 10,
      ),
    };
  });
  const lit = $derived(hovered == null ? null : (columns[hovered] ?? null));
  const sitting = $derived(
    cabinets.find((cabinet) => cabinet.from === seated) ?? null,
  );
  const label = (party: string | null) =>
    party == null
      ? m.record_none()
      : (parties.get(party)?.short ?? parties.get(party)?.label ?? "");
</script>

{#if record.isPending && isExpected}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-busy="true"
  >
    <h2 class="font-serif text-xl/tight font-[640] tracking-[-0.005em]">
      {m.record_title()}
    </h2>
    <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
      {m.record_lede()}
    </p>
    <div
      class="animate-pulse rounded-sm bg-muted block-65.5 motion-reduce:animate-none"
    ></div>
    <div class="block-10"></div>
  </section>
{:else if current != null || cabinets.length > 0}
  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="{uid}-title"
    data-export
  >
    <Export name="record" class="absolute inset-e-2 inset-bs-2" />
    <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 pe-8">
      <div class="grid gap-0.5">
        <h2
          id="{uid}-title"
          class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
        >
          {current == null ? m.record_governments() : m.record_title()}
        </h2>
        <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
          {current == null ? m.record_governments_lede() : m.record_lede()}
          {#if current != null && !current.isOffice}
            <a
              class="font-semibold whitespace-nowrap text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
              href={resolve("/id/[id]", { id: current.id })}
              >{m.chamber_open()}</a
            >
          {/if}
        </p>
      </div>
      {#if series.length > 1}
        <div class="flex flex-wrap" role="group" aria-label={m.record_tabs()}>
          {#each series as entry (entry.id)}
            <button
              type="button"
              class={cn(
                "-ms-px flex keycap items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                {
                  "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                    entry.id === current?.id,
                },
              )}
              aria-pressed={entry.id === current?.id}
              onclick={() => {
                chosen = entry.id;
                hovered = null;
              }}>{entry.name}</button
            >
          {/each}
        </div>
      {/if}
    </div>
    {#if current == null}
      <div class="min-inline-0" bind:clientWidth={width}>
        <svg
          class="block overflow-visible block-auto max-inline-full"
          viewBox="0 0 {width} 60"
          {width}
          height="60"
          role="img"
          aria-label={m.record_governments()}
        >
          {#each timeline.spans as span (span.from)}
            <rect
              x={span.x}
              y="6"
              width={span.w}
              height="30"
              fill={tint(span.party)}
              opacity={seated == null || seated === span.from
                ? span.isCaretaker
                  ? 0.5
                  : 1
                : 0.3}
              role="presentation"
              onpointerenter={() => (seated = span.from)}
              onpointerleave={() => (seated = null)}
            />
          {/each}
          {#each timeline.runs as run (run.from)}
            <text
              x={run.x + 5}
              y="25"
              class="pointer-events-none text-[0.6875rem] font-semibold"
              opacity={seated == null ? 1 : 0.4}
              style:fill={`color(from ${tint(run.party)} xyz-d65 calc(0.9505 * clamp(0, (0.179 - y) * 1e6, 1)) clamp(0, (0.179 - y) * 1e6, 1) calc(1.089 * clamp(0, (0.179 - y) * 1e6, 1)))`}
              >{run.label}</text
            >
          {/each}
          {#each timeline.years as year (year.year)}
            <line
              x1={year.x}
              x2={year.x}
              y1="38"
              y2="42"
              class="stroke-muted-foreground"
            />
            <text
              x={year.x}
              y="54"
              text-anchor="middle"
              class="fill-muted-foreground font-mono text-[0.625rem] tabular-nums"
              >{year.year}</text
            >
          {/each}
        </svg>
      </div>
    {:else}
      <div class="relative min-inline-0" bind:clientWidth={width}>
        <svg
          class="block overflow-visible block-auto max-inline-full"
          viewBox="0 0 {width} {height}"
          {width}
          {height}
          role="img"
          aria-label={current.name}
        >
          {#each columns as column (column.index)}
            <a
              href={column.path == null
                ? undefined
                : resolve("/[...path]", { path: column.path })}
              aria-label={column.name}
              onpointerenter={() => (hovered = column.index)}
              onpointerleave={() => (hovered = null)}
              onfocus={() => (hovered = column.index)}
              onblur={() => (hovered = null)}
            >
              <rect
                x={column.x}
                y={PAD.top}
                width={Math.max(step, 1)}
                height={inner}
                fill="transparent"
              />
              {#each column.blocks as block (block.party)}
                <rect
                  x={column.x + gap / 2}
                  y={block.y}
                  width={Math.max(step - gap, 0.6)}
                  height={Math.max(block.h - (step > 10 ? 0.5 : 0), 0)}
                  fill={tint(block.party)}
                  opacity={hovered == null || hovered === column.index
                    ? column.strength
                    : column.strength * 0.45}
                />
              {/each}
            </a>
          {/each}
          {#if !isSingle}
            <line
              x1={PAD.side}
              x2={PAD.side + step * columns.length}
              y1={PAD.top + inner / 2}
              y2={PAD.top + inner / 2}
              class="stroke-foreground"
              stroke-dasharray="3 3"
              stroke-opacity="0.55"
            />
          {/if}
          {#each spans as span (span.key)}
            <rect
              x={span.x}
              y={PAD.top + inner + 8}
              width={span.w}
              height="10"
              fill={tint(span.party)}
              opacity={seated == null || seated === span.from
                ? span.isCaretaker
                  ? 0.5
                  : 1
                : 0.3}
              role="presentation"
              onpointerenter={() => (seated = span.from)}
              onpointerleave={() => (seated = null)}
            />
          {/each}
          {#each ticks as column (column.index)}
            <text
              x={column.x + step / 2}
              y={height - 6}
              text-anchor={column.index === 0
                ? "start"
                : column.index === columns.length - 1
                  ? "end"
                  : "middle"}
              class="fill-muted-foreground font-mono text-[0.625rem] tabular-nums"
              >{column.year}</text
            >
          {/each}
        </svg>
        {#if lit != null}
          <div
            class="pointer-events-none absolute grid gap-1 rounded-sm bg-card/95 px-2.5 py-2 text-xs shadow-menu ring-1 ring-border inline-60"
            style:inset-inline-start="{Math.max(
              0,
              Math.min(width - 240, lit.x + step + 8),
            )}px"
            style:inset-block-start="{PAD.top}px"
          >
            <span class="text-[0.8125rem] font-semibold">{lit.name}</span>
            {#if isSingle}
              {const winner = $derived(lit.parties[0]?.party ?? null)}
              <span class="flex items-center gap-1.5">
                <span
                  class="rounded-xs block-2.5 inline-2.5"
                  style:background={tint(winner)}
                ></span>
                {label(winner)}
              </span>
              {#if lit.share != null}
                <span class="font-mono tabular-nums"
                  >{m.record_share({ share: percent(lit.share) })}</span
                >
              {/if}
            {:else}
              <ul class="grid gap-0.5">
                {#each lit.parties.slice(0, 5) as entry (entry.party)}
                  <li class="flex items-center justify-between gap-3">
                    <span class="flex items-center gap-1.5 truncate">
                      <span
                        class="shrink-0 rounded-xs block-2.5 inline-2.5"
                        style:background={tint(entry.party)}
                      ></span>
                      <span class="truncate">{label(entry.party)}</span>
                    </span>
                    <span class="font-mono font-semibold tabular-nums"
                      >{count(entry.seats)}</span
                    >
                  </li>
                {/each}
              </ul>
              <span class="text-muted-foreground"
                >{current.isOffice
                  ? m.record_electors({ count: count(lit.filled) })
                  : m.record_seats({
                      seats: count(lit.parties[0]?.seats ?? 0),
                      total: count(lit.filled),
                    })}</span
              >
            {/if}
          </div>
        {/if}
      </div>
    {/if}
    {#if cabinets.length > 0}
      <p
        class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[0.8125rem] min-block-5"
        aria-live="polite"
      >
        {#if sitting == null}
          {#if current != null}
            <span class="font-semibold">{m.record_governments()}</span>
            <span class="text-muted-foreground"
              >{m.record_governments_key()}</span
            >
          {/if}
        {:else}
          <span class="flex items-center gap-1.5 font-semibold">
            <span
              class="rounded-xs block-2.5 inline-2.5"
              style:background={tint(sitting.party)}
            ></span>
            {sitting.name}
          </span>
          {#if sitting.head != null}
            <span>{m.record_governments_head({ name: sitting.head })}</span>
          {/if}
          <span class="font-mono text-xs text-muted-foreground tabular-nums"
            >{date(sitting.from)} – {sitting.to == null
              ? m.governments_present()
              : date(sitting.to)}</span
          >
          {#if sitting.isCaretaker}
            <span class="highlighter px-1 text-xs font-semibold"
              >{m.governments_caretaker()}</span
            >
          {/if}
        {/if}
      </p>
    {/if}
    <ul class="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
      {#each legend as party (party)}
        <li class="flex items-center gap-1.5">
          <span
            class="rounded-xs block-2.5 inline-2.5"
            style:background={tint(party)}
          ></span>
          {#if party == null}
            <span class="text-muted-foreground">{label(party)}</span>
          {:else}
            <a class="hover:underline" href={resolve("/id/[id]", { id: party })}
              >{label(party)}</a
            >
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{/if}
