<script lang="ts">
  import { resolve } from "$app/paths";
  import { percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const { data }: { data: NonNullable<PageData["places"]> } = $props();

  const PAD = { top: 12, right: 12, bottom: 24, left: 40 };
  const YES = /^(?:yes|ja|oui|sì|si|evet)$/iu;

  let width = $state(720);
  let hovered = $state<{ series: number; point: number } | null>(null);

  const parties = $derived(
    new Map(data.parties.map((party) => [party.id, party])),
  );

  const races = $derived(
    data.series
      .filter((series) => !series.isQuestion)
      .map((series) => {
        const tops = Map.groupBy(
          series.points.flatMap((point, at) =>
            (point.sides.find((side) => side.length > 0) ?? [])
              .slice(0, 2)
              .map((entry) => ({ ...entry, at })),
          ),
          (entry) => entry.key,
        )
          .entries()
          .map(([key, entries]) => ({
            key,
            hits: entries.length,
            last: Math.max(...entries.map((entry) => entry.at)),
            entry: entries.at(-1),
          }))
          .toArray()
          .toSorted((a, b) => b.hits - a.hits || b.last - a.last)
          .slice(0, 2)
          .map((top) => {
            const party =
              top.entry?.party == null ? null : parties.get(top.entry.party);
            return {
              key: top.key,
              label: party?.short ?? party?.label ?? top.entry?.label ?? "",
              color: swatch(party?.color ?? top.entry?.color ?? null, top.key),
            };
          });
        const [first, second] = tops;
        const rows = series.points.map((point) => ({
          ...point,
          margins: point.sides.map((side) => {
            if (side.length === 0 || first == null || second == null) {
              return null;
            }
            return (
              (side.find((entry) => entry.key === first.key)?.share ?? 0) -
              (side.find((entry) => entry.key === second.key)?.share ?? 0)
            );
          }),
        }));
        const reach = Math.max(
          0.1,
          ...rows.flatMap((row) =>
            row.margins.flatMap((value) =>
              value == null ? [] : [Math.abs(value)],
            ),
          ),
        );
        return {
          name: series.name ?? "",
          first,
          second,
          rows,
          top: Math.ceil(reach * 10) / 10,
          low: Math.min(...rows.map((row) => row.year)),
          high: Math.max(...rows.map((row) => row.year)),
        };
      })
      .filter((race) => race.first != null && race.second != null),
  );

  const questions = $derived(
    data.series
      .filter((series) => series.isQuestion)
      .flatMap((series) => series.points)
      .map((point) => ({
        ...point,
        yes: point.sides.map(
          (side) => side.find((entry) => YES.test(entry.label))?.share ?? null,
        ),
      }))
      .toSorted((a, b) => b.date.localeCompare(a.date)),
  );

  const names = $derived([
    m.versus_overall(),
    ...data.places.map((place) => place.name),
  ]);
  const height = $derived(
    Math.round(Math.min(300, Math.max(200, width * 0.34))),
  );
  const sx = (race: { low: number; high: number }, year: number) =>
    race.high === race.low
      ? PAD.left + (width - PAD.left - PAD.right) / 2
      : PAD.left +
        ((year - race.low) / (race.high - race.low)) *
          (width - PAD.left - PAD.right);
  const sy = (race: { top: number }, value: number) =>
    PAD.top +
    ((race.top - value) / (2 * race.top)) * (height - PAD.top - PAD.bottom);
  const line = (race: (typeof races)[number], side: number) =>
    race.rows
      .flatMap((row) => {
        const value = row.margins[side];
        return value == null
          ? []
          : [`${sx(race, row.year).toFixed(1)},${sy(race, value).toFixed(1)}`];
      })
      .join(" ");
  const STROKES = ["2 3", "", "5 3"];
</script>

{#snippet margin(
  race: (typeof races)[number],
  value: number | null,
  isStrong = false,
  isLabeled = true,
)}
  {#if value == null}
    <span class="text-muted-foreground">–</span>
  {:else if Math.abs(value) < 0.0005}
    <span>{m.versus_even()}</span>
  {:else}
    {const lead = $derived(value > 0 ? race.first : race.second)}
    <span
      class="inline-flex items-center justify-end gap-1.5 whitespace-nowrap"
    >
      <span
        class="shrink-0 rounded-xs block-2 inline-2"
        style:background={lead?.color}
      ></span>
      <span
        class={cn("truncate font-sans text-xs text-muted-foreground", {
          "max-sm:sr-only": isLabeled,
          "sr-only": !isLabeled,
        })}>{lead?.label}</span
      >
      <span class={cn({ "font-semibold": isStrong })}
        >{points(Math.abs(value)).replace(/^-/u, "+")}</span
      >
    </span>
  {/if}
{/snippet}

{#if races.length === 0 && questions.length === 0}
  <p class="col-span-12 bg-card px-3.5 py-4 text-sm text-muted-foreground">
    {m.versus_places_none()}
  </p>
{/if}

{#each races as race, index (race.name)}
  {const focus = $derived(
    hovered?.series === index ? (race.rows[hovered.point] ?? null) : null,
  )}
  <section
    class="col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="race-{index}"
  >
    <div class="grid gap-0.5">
      <h2
        id="race-{index}"
        class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
      >
        {race.name}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground">
        {m.versus_margin_key({
          first: race.first?.label ?? "",
          second: race.second?.label ?? "",
        })}
      </p>
    </div>
    <div class="flex flex-wrap gap-x-5 gap-y-1 text-[0.8125rem]">
      {#each names as name, side (side)}
        <span class="inline-flex items-center gap-2">
          <svg
            class="block-2.5 inline-6"
            viewBox="0 0 24 10"
            aria-hidden="true"
          >
            <line
              x1="0"
              x2="24"
              y1="5"
              y2="5"
              class={cn("stroke-foreground", {
                "stroke-muted-foreground": side === 0,
              })}
              stroke-width={side === 0 ? 1.25 : 2}
              stroke-dasharray={STROKES[side]}
            />
            {#if side === 1}
              <circle cx="12" cy="5" r="3.5" class="fill-foreground" />
            {:else if side === 2}
              <rect x="9" y="2" width="6" height="6" class="fill-foreground" />
            {/if}
          </svg>
          <span class={cn({ "font-semibold": side > 0 })}>{name}</span>
          {#if focus != null}
            <span class="font-mono text-xs tabular-nums"
              >{@render margin(race, focus.margins[side] ?? null)}</span
            >
          {/if}
        </span>
      {/each}
      {#if focus != null}
        <span class="ms-auto font-mono text-xs text-muted-foreground"
          >{focus.year}</span
        >
      {/if}
    </div>
    <div bind:clientWidth={width} class="min-inline-0">
      <svg
        viewBox="0 0 {width} {height}"
        class="block overflow-visible inline-full"
        style:block-size="{height}px"
        role="img"
        aria-label={race.name}
        onpointerleave={() => (hovered = null)}
        onpointermove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const x = event.clientX - box.left;
          let best = { point: 0, distance: Infinity };
          for (const [point, row] of race.rows.entries()) {
            const distance = Math.abs(sx(race, row.year) - x);
            if (distance < best.distance) {
              best = { point, distance };
            }
          }
          hovered = { series: index, point: best.point };
        }}
      >
        <rect
          x={PAD.left}
          y={PAD.top}
          width={width - PAD.left - PAD.right}
          height={sy(race, 0) - PAD.top}
          fill={race.first?.color}
          opacity="0.07"
        />
        <rect
          x={PAD.left}
          y={sy(race, 0)}
          width={width - PAD.left - PAD.right}
          height={height - PAD.bottom - sy(race, 0)}
          fill={race.second?.color}
          opacity="0.07"
        />
        {#each [race.top, race.top / 2, 0, -race.top / 2, -race.top] as tick (tick)}
          <line
            x1={PAD.left}
            x2={width - PAD.right}
            y1={sy(race, tick)}
            y2={sy(race, tick)}
            class={cn("stroke-border", { "stroke-foreground/40": tick === 0 })}
          />
          <text
            x={PAD.left - 6}
            y={sy(race, tick) + 3.5}
            text-anchor="end"
            class="fill-muted-foreground font-mono text-[0.625rem]"
            >{tick === 0 ? "0" : Math.round(Math.abs(tick) * 100)}</text
          >
        {/each}
        {#each race.rows as row, point (row.date)}
          <text
            x={sx(race, row.year)}
            y={height - 6}
            text-anchor="middle"
            class={cn("fill-muted-foreground font-mono text-[0.625rem]", {
              "max-sm:hidden": point % 2 === 1 && race.rows.length > 6,
              "fill-foreground": focus === row,
            })}>{row.year}</text
          >
        {/each}
        {#if focus != null}
          <line
            x1={sx(race, focus.year)}
            x2={sx(race, focus.year)}
            y1={PAD.top}
            y2={height - PAD.bottom}
            class="stroke-foreground/30"
          />
        {/if}
        {#each [0, 1, 2] as side (side)}
          <polyline
            points={line(race, side)}
            fill="none"
            class={cn("stroke-foreground", {
              "stroke-muted-foreground": side === 0,
            })}
            stroke-width={side === 0 ? 1.25 : 2}
            stroke-dasharray={STROKES[side]}
            stroke-linejoin="round"
          />
        {/each}
        {#each race.rows as row (row.date)}
          {#each [1, 2] as side (side)}
            {const value = $derived(row.margins[side] ?? null)}
            {#if value != null}
              {const tint = $derived(
                value >= 0 ? race.first?.color : race.second?.color,
              )}
              {const r = $derived(focus === row ? 5 : 3.5)}
              {#if side === 1}
                <circle
                  cx={sx(race, row.year)}
                  cy={sy(race, value)}
                  {r}
                  fill={tint}
                />
              {:else}
                <rect
                  x={sx(race, row.year) - r * 0.9}
                  y={sy(race, value) - r * 0.9}
                  width={r * 1.8}
                  height={r * 1.8}
                  fill={tint}
                />
              {/if}
            {/if}
          {/each}
        {/each}
      </svg>
    </div>
    <div class="overflow-x-auto">
      <table class="border-collapse text-sm tabular-nums inline-full">
        <thead>
          <tr class="border-be text-xs text-muted-foreground">
            <th scope="col" class="py-1.5 pe-3 text-start font-semibold"
              >{m.versus_year()}</th
            >
            {#each names as name, side (side)}
              <th
                scope="col"
                class={cn("px-2 py-1.5 text-end font-semibold", {
                  "max-sm:hidden": side === 0,
                })}>{name}</th
              >
            {/each}
            <th scope="col" class="py-1.5 ps-2 text-end font-semibold"
              >{m.versus_gap()}</th
            >
          </tr>
        </thead>
        <tbody>
          {#each race.rows.toReversed() as row (row.date)}
            {const a = $derived(row.margins[1] ?? null)}
            {const b = $derived(row.margins[2] ?? null)}
            <tr
              class={cn("border-be border-border/60", {
                "bg-secondary": focus === row,
              })}
            >
              <th scope="row" class="py-1.5 pe-3 text-start font-normal">
                {#if row.path == null}
                  <span class="font-mono">{row.year}</span>
                {:else}
                  <a
                    class="font-mono hover:highlighter"
                    href={resolve("/[...path]", { path: row.path })}
                    title={row.name}>{row.year}</a
                  >
                {/if}
              </th>
              {#each [0, 1, 2] as side (side)}
                <td
                  class={cn("px-2 py-1.5 text-end font-mono text-[0.8125rem]", {
                    "max-sm:hidden": side === 0,
                    "text-muted-foreground": side === 0,
                  })}
                  >{@render margin(
                    race,
                    row.margins[side] ?? null,
                    side > 0,
                  )}</td
                >
              {/each}
              <td class="py-1.5 ps-2 text-end font-mono text-[0.8125rem]"
                >{@render margin(
                  race,
                  a == null || b == null ? null : a - b,
                  false,
                  false,
                )}</td
              >
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </section>
{/each}

{#if questions.length > 0}
  <section
    class="col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="questions"
  >
    <div class="grid gap-0.5">
      <h2
        id="questions"
        class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
      >
        {m.versus_questions()}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground">
        {m.versus_questions_key()}
      </p>
    </div>
    <ol class="grid">
      {#each questions as question (question.path ?? question.name)}
        <li
          class="grid grid-cols-[minmax(0,1fr)_minmax(0,16rem)] items-center gap-x-4 gap-y-1 border-be border-border/60 py-2 last:border-be-0 max-sm:grid-cols-[minmax(0,1fr)]"
        >
          <span class="grid gap-0.5 min-inline-0">
            {#if question.path == null}
              <span class="font-semibold text-pretty">{question.name}</span>
            {:else}
              <a
                class="font-semibold text-pretty hover:highlighter"
                href={resolve("/[...path]", { path: question.path })}
                >{question.name}</a
              >
            {/if}
            <span class="font-mono text-xs text-muted-foreground"
              >{question.year}</span
            >
          </span>
          <span class="relative block-6" aria-hidden="true">
            <span class="absolute inset-x-0 inset-bs-1/2 bg-border block-px"
            ></span>
            <span
              class="absolute inset-y-1 bg-foreground/40 inline-px"
              style:inset-inline-start="50%"
            ></span>
            {#each [1, 2] as side (side)}
              {const value = $derived(question.yes[side] ?? null)}
              {#if value != null}
                <span
                  class={cn(
                    "absolute inset-bs-1/2 -translate-1/2  bg-foreground block-2.5 inline-2.5",
                    { "rounded-full": side === 1 },
                  )}
                  style:inset-inline-start="{value * 100}%"
                ></span>
              {/if}
            {/each}
          </span>
          <span
            class="col-start-2 flex justify-between font-mono text-xs tabular-nums max-sm:col-start-1"
          >
            {#each [1, 2] as side (side)}
              <span class="inline-flex items-center gap-1.5">
                <span
                  class={cn("bg-foreground block-2 inline-2", {
                    "rounded-full": side === 1,
                  })}
                ></span>
                <span class="sr-only">{names[side]}</span>
                {question.yes[side] == null
                  ? "–"
                  : percent(question.yes[side] ?? 0)}
              </span>
            {/each}
          </span>
        </li>
      {/each}
    </ol>
  </section>
{/if}
