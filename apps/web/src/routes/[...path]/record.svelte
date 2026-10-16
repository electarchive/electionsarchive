<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const { id }: { id: string } = $props();

  const uid = $props.id();
  const PAD = { top: 8, bottom: 22, side: 2 };

  const record = createQuery(() =>
    orpc.polities.record.queryOptions({ input: { id } }),
  );

  let chosen = $state<string | null>(null);
  let width = $state(960);
  let hovered = $state<number | null>(null);

  const series = $derived(record.data?.series ?? []);
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
  const height = $derived(isSingle ? 96 : 240);
  const step = $derived(
    current == null
      ? 0
      : Math.min(
          72,
          (width - PAD.side * 2) / Math.max(1, current.points.length),
        ),
  );
  const gap = $derived(step > 10 ? 2 : step > 4 ? 1 : 0);
  const inner = $derived(height - PAD.top - PAD.bottom);
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
    Map.groupBy(
      (current?.points ?? []).flatMap((point) =>
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
  const lit = $derived(hovered == null ? null : (columns[hovered] ?? null));
  const label = (party: string | null) =>
    party == null
      ? m.record_none()
      : (parties.get(party)?.short ?? parties.get(party)?.label ?? "");
</script>

{#if series.length > 0 && current != null}
  <section
    class="col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="{uid}-title"
  >
    <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
      <div class="grid gap-0.5">
        <h2
          id="{uid}-title"
          class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
        >
          {m.record_title()}
        </h2>
        <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
          {m.record_lede()}
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
                    entry.id === current.id,
                },
              )}
              aria-pressed={entry.id === current.id}
              onclick={() => {
                chosen = entry.id;
                hovered = null;
              }}>{entry.name}</button
            >
          {/each}
        </div>
      {/if}
    </div>
    <div class="relative min-inline-0" bind:clientWidth={width}>
      <svg
        class="block overflow-visible"
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
