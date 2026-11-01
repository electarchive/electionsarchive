<script lang="ts">
  import { resolve } from "$app/paths";
  import Export from "#lib/components/export.svelte";
  import { fits } from "#lib/fits.js";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Duel from "./duel.svelte";
  import Ledger from "./ledger.svelte";
  import Tape from "./tape.svelte";

  type Data = NonNullable<Awaited<PageData["body"]>["parties"]>;
  type Side = Data["series"][number]["points"][number]["sides"][number];

  const {
    data,
    first,
  }: { data: Data; first: { id: string; name: string } | undefined } = $props();

  const DAY = 86_400_000;

  const names = $derived(
    data.parties.map((party) => party.short ?? party.name),
  );
  const a = $derived(names[0] ?? "");
  const b = $derived(names[1] ?? "");
  const tones = $derived(
    data.parties.map((party) => swatch(party.color, `party:${party.id}`)),
  );
  const toneA = $derived(tones[0] ?? "var(--foreground)");
  const toneB = $derived(tones[1] ?? "var(--foreground)");

  const charts = $derived(
    data.series.map((series, index) => {
      const rows = series.points.map((point, at) => ({
        ...point,
        key: `${index}:${at}`,
        x:
          point.year +
          (Date.parse(`${point.date}T00:00:00Z`) - Date.UTC(point.year, 0, 1)) /
            DAY /
            366,
      }));
      let top = 0;
      for (const row of rows) {
        for (const side of row.sides) {
          top = Math.max(top, side?.share ?? 0);
        }
      }
      return {
        index,
        id: series.id,
        name: series.name,
        rows,
        top: Math.max(0.1, Math.ceil(top * 10) / 10),
        peak: Math.max(0.01, top),
      };
    }),
  );

  const summary = $derived.by(() => {
    const ahead = [0, 0];
    const best = [0, 0];
    const sums = [0, 0];
    const seats = [0, 0];
    let total = 0;
    let latest: (typeof charts)[number]["rows"][number] | null = null;
    for (const chart of charts) {
      for (const row of chart.rows) {
        const [one, two] = row.sides;
        for (const [side, entry] of row.sides.entries()) {
          if (entry != null) {
            best[side] = Math.max(best[side] ?? 0, entry.share);
            seats[side] = (seats[side] ?? 0) + (entry.seats ?? 0);
          }
        }
        if (one == null || two == null) {
          continue;
        }
        total += 1;
        sums[0] = (sums[0] ?? 0) + one.share;
        sums[1] = (sums[1] ?? 0) + two.share;
        ahead[one.share >= two.share ? 0 : 1] =
          (ahead[one.share >= two.share ? 0 : 1] ?? 0) + 1;
        if (latest == null || row.date > latest.date) {
          latest = row;
        }
      }
    }
    return { ahead, best, sums, seats, total, latest };
  });

  const tape = $derived.by(() => {
    const { ahead, best, sums, seats, total, latest } = summary;
    const mean = (side: number) =>
      total === 0 ? 0 : (sums[side] ?? 0) / total;
    const last = (side: number) => latest?.sides[side]?.share ?? null;
    return [
      {
        label: m.versus_tape_ahead(),
        values: [ahead[0] ?? 0, ahead[1] ?? 0],
        text: [count(ahead[0] ?? 0), count(ahead[1] ?? 0)],
      },
      {
        label: m.versus_tape_average(),
        values: [mean(0), mean(1)],
        text: [percent(mean(0)), percent(mean(1))],
      },
      {
        label: m.versus_tape_best(),
        values: [best[0] ?? 0, best[1] ?? 0],
        text: [percent(best[0] ?? 0), percent(best[1] ?? 0)],
      },
      {
        label: m.versus_tape_latest({ year: String(latest?.year ?? "") }),
        values: [last(0), last(1)],
        text: [
          last(0) == null ? "–" : percent(last(0) ?? 0),
          last(1) == null ? "–" : percent(last(1) ?? 0),
        ],
      },
      {
        label: m.versus_tape_seats(),
        values: [seats[0] ?? 0, seats[1] ?? 0],
        text: [count(seats[0] ?? 0), count(seats[1] ?? 0)],
      },
    ] satisfies Array<{
      label: string;
      values: [number | null, number | null];
      text: [string, string];
    }>;
  });

  const cells = $derived(
    charts.flatMap((chart) => {
      const years = new Map<number, (typeof chart.rows)[number]>();
      for (const row of chart.rows) {
        years.set(row.year, row);
      }
      return [...years.values()].map((row) => ({
        key: row.key,
        row: chart.index,
        year: row.year,
        href: row.path,
        chart,
        point: row,
        halves: row.sides.map((side, index) =>
          side == null
            ? null
            : {
                color: tones[index] ?? "var(--foreground)",
                strength: Math.min(1, side.share / chart.peak),
              },
        ) as [
          { color: string; strength: number } | null,
          { color: string; strength: number } | null,
        ],
      }));
    }),
  );

  const actives = $state<Record<number, string | null>>({});
</script>

{#snippet marker(side: number)}
  <span
    class={cn("inline-block shrink-0 block-2 inline-2", {
      "rounded-full": side === 0,
    })}
    style:background-color={side === 0 ? toneA : toneB}
    aria-hidden="true"
  ></span>
{/snippet}

{#snippet share(side: Side, isStrong = false)}
  {#if side == null}
    <span class="text-muted-foreground">–</span>
  {:else}
    <span class={cn("font-figures tabular-nums", { "font-semibold": isStrong })}
      >{percent(side.share)}</span
    >
    {#if side.seats != null && (side.filled ?? 0) > 1}
      <span class="ms-1 font-figures text-xs text-muted-foreground tabular-nums"
        >{m.lists_seats({ count: side.seats })}</span
      >
    {/if}
  {/if}
{/snippet}

{#snippet gap(sides: Array<Side>)}
  {const one = $derived(sides[0])}
  {const two = $derived(sides[1])}
  {#if one == null || two == null}
    <span class="text-muted-foreground">–</span>
  {:else if Math.abs(one.share - two.share) < 0.0005}
    {m.versus_even()}
  {:else}
    <span class="inline-flex items-center gap-1.5 font-figures tabular-nums">
      {@render marker(one.share > two.share ? 0 : 1)}{points(
        Math.abs(one.share - two.share),
      )}
    </span>
  {/if}
{/snippet}

{#if charts.length === 0}
  <p class="col-span-12 bg-card px-3.5 py-4 text-sm text-muted-foreground">
    {m.versus_parties_none()}
    {#if first != null}
      <a
        class="mbs-1 block font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        href={resolve(`/compare?a=${first.id}`)}
        >{m.versus_none_rivals({ name: first.name })}</a
      >
    {/if}
  </p>
{:else}
  <section
    class="col-span-12 grid justify-items-center gap-4 bg-card px-3.5 pbs-4 pbe-5"
  >
    {#if summary.total > 0}
      <p
        class="text-center text-lg/snug font-semibold text-balance max-inline-[60ch]"
      >
        {m.versus_verdict_parties({
          a,
          b,
          one: count(summary.ahead[0] ?? 0),
          total: count(summary.total),
        })}
      </p>
    {/if}
    <div class="inline-full max-inline-3xl">
      <Tape names={[a, b]} colors={[toneA, toneB]} rows={tape} />
    </div>
  </section>

  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-4 pbe-4"
    aria-labelledby="ledger"
  >
    <div class="grid gap-0.5 pe-10">
      <h2 id="ledger" class="heading">{m.versus_ledger_title()}</h2>
      <p class="text-xs text-pretty text-muted-foreground">
        {m.versus_ledger_parties_key({ a, b })}
      </p>
    </div>
    <Ledger
      rows={charts.map((chart) => chart.name)}
      {cells}
      label={m.versus_ledger_title()}
    >
      {#snippet readout(cell: (typeof cells)[number])}
        <div class="grid gap-1 text-[0.8125rem]">
          <p class="flex flex-wrap items-baseline gap-x-2">
            <span class="font-figures font-bold tabular-nums">{cell.year}</span>
            <span class="font-semibold">{cell.chart.name}</span>
            {#if cell.point.path != null}
              <a
                class="truncate text-xs text-muted-foreground underline decoration-current/40 underline-offset-2 hover:text-foreground"
                href={resolve("/[...path]", { path: cell.point.path })}
                >{cell.point.name}</a
              >
            {/if}
          </p>
          <div class="flex flex-wrap items-center gap-x-5 gap-y-0.5">
            {#each cell.point.sides as side, index (index)}
              <span class="flex items-center gap-1.5">
                {@render marker(index)}<span class="font-semibold"
                  >{names[index]}</span
                >
                {@render share(
                  side,
                  side != null &&
                    side.share >= (cell.point.sides[1 - index]?.share ?? 0),
                )}
              </span>
            {/each}
            <span class="text-muted-foreground"
              >{@render gap(cell.point.sides)}</span
            >
          </div>
        </div>
      {/snippet}
    </Ledger>
  </section>

  {#each charts as chart (chart.id)}
    {const ordered = $derived(chart.rows.toReversed())}
    <section
      class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-4 pbe-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:gap-x-6"
      aria-labelledby="party-series-{chart.index}"
      data-export
    >
      <div class="grid content-start gap-3">
        <div class="relative grid gap-1 pe-10">
          <Export
            name="series-{chart.index}"
            class="absolute inset-e-0 inset-bs-0"
          />
          <h2 id="party-series-{chart.index}" class="heading">{chart.name}</h2>
          <p class="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {#each names as name, side (side)}
              <span class="flex items-center gap-1.5">
                <svg width="22" height="8" aria-hidden="true">
                  <line
                    x1="0"
                    x2="22"
                    y1="4"
                    y2="4"
                    style:stroke={side === 0 ? toneA : toneB}
                    stroke-width="2"
                    stroke-dasharray={side === 0 ? null : "5 3"}
                  />
                </svg>{name}</span
              >
            {/each}
          </p>
        </div>
        <Duel
          points={chart.rows.map((row) => ({
            key: row.key,
            x: row.x,
            a: row.sides[0]?.share ?? null,
            b: row.sides[1]?.share ?? null,
            ref: null,
            marks: [null, null],
          }))}
          colors={[toneA, toneB]}
          fills={[toneA, toneB]}
          low={0}
          high={chart.top}
          format={(value: number) => String(Math.round(value * 100))}
          bind:active={actives[chart.index]}
        >
          {#snippet readout(point: { key: string })}
            {const row = $derived(
              chart.rows.find((entry) => entry.key === point.key),
            )}
            {#if row != null}
              <p class="flex items-baseline gap-2">
                <span class="font-figures font-bold tabular-nums"
                  >{row.year}</span
                >
                <span class="truncate text-muted-foreground">{row.name}</span>
              </p>
              <div
                class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2"
              >
                {#each row.sides as side, index (index)}
                  <span class="flex items-center gap-1.5"
                    >{@render marker(index)}{names[index]}</span
                  >
                  <span>{@render share(side)}</span>
                {/each}
              </div>
            {/if}
          {/snippet}
        </Duel>
      </div>
      <div
        class="relative overflow-x-auto overflow-y-hidden overscroll-x-contain min-inline-0 not-data-fits:overflow-y-auto not-data-fits:max-block-104 data-fits:overflow-visible lg:overflow-y-auto lg:max-block-104"
        {@attach fits}
      >
        <table class="text-[0.8125rem] inline-full">
          <thead
            class="sticky inset-bs-0 z-2 bg-background text-xs text-muted-foreground [&_th]:shadow-[inset_0_-1px_0_var(--color-border)]"
          >
            <tr>
              <th class="pe-2 text-start font-semibold block-7"
                >{m.versus_year()}</th
              >
              {#each names as name, side (side)}
                <th class="pe-2 text-start font-semibold">
                  <span class="flex items-center gap-1.5"
                    >{@render marker(side)}<span class="truncate">{name}</span
                    ></span
                  >
                </th>
              {/each}
              <th
                class="text-end font-semibold"
                title={m.versus_gap_help({ a, b })}>{m.versus_gap()}</th
              >
            </tr>
          </thead>
          <tbody>
            {#each ordered as row (row.key)}
              {const one = $derived(row.sides[0]?.share ?? -1)}
              {const two = $derived(row.sides[1]?.share ?? -1)}
              <tr
                class={cn("border-be border-border", {
                  highlighter: actives[chart.index] === row.key,
                })}
                onpointerenter={() => (actives[chart.index] = row.key)}
              >
                <td class="pe-2 font-figures tabular-nums block-7">
                  {#if row.path == null}
                    {row.year}
                  {:else}
                    <a
                      class="underline decoration-current/30 underline-offset-2 hover:decoration-current"
                      href={resolve("/[...path]", { path: row.path })}
                      title={row.name}>{row.year}</a
                    >
                  {/if}
                </td>
                <td class="pe-2"
                  >{@render share(row.sides[0] ?? null, one > two)}</td
                >
                <td class="pe-2"
                  >{@render share(row.sides[1] ?? null, two > one)}</td
                >
                <td class="text-end">{@render gap(row.sides)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>
  {/each}
{/if}
