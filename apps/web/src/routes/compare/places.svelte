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

  type Body = Awaited<PageData["body"]>;
  type Entry = { k: number; share: number; who: string | null };

  const { data }: { data: NonNullable<Body["places"]> } = $props();

  const YES = /^(?:yes|ja|oui|sì|si|evet)$/iu;
  const INK = "var(--foreground)";
  const DAY = 86_400_000;

  const names = $derived(data.places.map((place) => place.name));
  const a = $derived(names[0] ?? "");
  const b = $derived(names[1] ?? "");
  const keys = $derived(
    data.keys.map((entry) => ({
      ...entry,
      color: swatch(entry.color, entry.key),
    })),
  );
  const label = (k: number) => keys[k]?.label ?? "";
  const tint = (k: number) => keys[k]?.color ?? INK;

  function lead(side: Array<Entry>) {
    const [one, two] = side.toSorted((x, y) => y.share - x.share);
    return one == null ? null : { ...one, lead: one.share - (two?.share ?? 0) };
  }

  const races = $derived(
    data.series
      .filter((series) => !series.isQuestion)
      .flatMap((series, index) => {
        const hits = new Map<number, { count: number; last: number }>();
        for (const [at, point] of series.points.entries()) {
          for (const side of point.sides.slice(1)) {
            for (const entry of side
              .toSorted((x, y) => y.share - x.share)
              .slice(0, 2)) {
              const hit = hits.get(entry.k) ?? { count: 0, last: 0 };
              hits.set(entry.k, { count: hit.count + 1, last: at });
            }
          }
        }
        const pair = [...hits]
          .toSorted((x, y) => y[1].count - x[1].count || y[1].last - x[1].last)
          .slice(0, 2)
          .map(([k]) => k)
          .toSorted((x, y) => label(x).localeCompare(label(y)));
        const [first, second] = pair;
        if (first == null || second == null) {
          return [];
        }
        const rows = series.points.map((point, at) => {
          const margins = point.sides.map((side) => {
            const top = side.toSorted((x, y) => y.share - x.share).slice(0, 2);
            const one = top.find((entry) => entry.k === first);
            const two = top.find((entry) => entry.k === second);
            return one == null || two == null ? null : one.share - two.share;
          });
          return {
            ...point,
            key: `${index}:${at}`,
            x:
              point.year +
              (Date.parse(point.date) - Date.UTC(point.year, 0, 1)) / DAY / 366,
            margins,
            winners: point.sides.map((side) => lead(side)),
          };
        });
        const hasRef = rows.some(
          (row) =>
            row.margins[0] != null &&
            (row.margins[1] == null ||
              Math.abs(row.margins[0] - row.margins[1]) > 1e-6),
        );
        const reach = Math.max(
          0.1,
          ...rows.flatMap((row) =>
            row.margins.flatMap((value) =>
              value == null ? [] : [Math.abs(value)],
            ),
          ),
        );
        return [
          {
            index,
            name: series.name ?? m.versus_governor(),
            kind: series.kind,
            first,
            second,
            rows,
            hasRef,
            top: Math.ceil(reach * 10) / 10,
          },
        ];
      }),
  );

  const questions = $derived(
    data.series
      .filter((series) => series.isQuestion)
      .flatMap((series) => series.points)
      .map((point, at) => ({
        ...point,
        key: `q:${at}`,
        yes: point.sides.map(
          (side) =>
            side.find((entry) => YES.test(label(entry.k)))?.share ?? null,
        ),
      }))
      .filter((point) => point.yes[1] != null && point.yes[2] != null)
      .toSorted((x, y) => y.date.localeCompare(x.date)),
  );

  const summary = $derived.by(() => {
    let same = 0;
    let total = 0;
    let split: { date: string; year: number; race: string } | null = null;
    const wins = [new Map<number, number>(), new Map<number, number>()];
    const leads: Array<Array<number>> = [[], []];
    const drift: Array<Array<number>> = [[], []];
    const held = [0, 0];
    for (const race of races) {
      for (const row of race.rows) {
        for (const side of [0, 1]) {
          const won = row.winners[side + 1];
          if (won != null) {
            held[side] = (held[side] ?? 0) + 1;
            leads[side]?.push(won.lead);
            const map = wins[side];
            map?.set(won.k, (map.get(won.k) ?? 0) + 1);
          }
          const own = row.margins[side + 1];
          const whole = row.margins[0];
          if (race.hasRef && own != null && whole != null) {
            drift[side]?.push(Math.abs(own - whole));
          }
        }
        const one = row.winners[1];
        const two = row.winners[2];
        if (one == null || two == null) {
          continue;
        }
        total += 1;
        if (one.k === two.k) {
          same += 1;
        } else if (split == null || row.date > split.date) {
          split = { date: row.date, year: row.year, race: race.name };
        }
      }
    }
    const tally = new Map<number, number>();
    for (const map of wins) {
      for (const [k, value] of map) {
        tally.set(k, (tally.get(k) ?? 0) + value);
      }
    }
    const parties = [...tally]
      .toSorted((x, y) => y[1] - x[1])
      .slice(0, 2)
      .map(([k]) => k);
    const mean = (values: Array<number>) => {
      let sum = 0;
      for (const value of values) {
        sum += value;
      }
      return values.length === 0 ? null : sum / values.length;
    };
    return {
      same,
      total,
      split,
      wins,
      leads: leads.map((values) => mean(values)),
      drift: drift.map((values) => mean(values)),
      held,
      parties,
      close: leads.map(
        (values) => values.filter((value) => value < 0.05).length,
      ),
    };
  });

  const passed = $derived(
    [1, 2].map(
      (side) =>
        questions.filter((point) => (point.yes[side] ?? 0) > 0.5).length,
    ),
  );

  const tape = $derived([
    ...summary.parties.map((k) => {
      const values: [number, number] = [
        summary.wins[0]?.get(k) ?? 0,
        summary.wins[1]?.get(k) ?? 0,
      ];
      return {
        label: m.versus_tape_wins({ party: label(k) }),
        values,
        text: [count(values[0]), count(values[1])] satisfies [string, string],
      };
    }),
    ...(summary.leads[0] == null || summary.leads[1] == null
      ? []
      : [
          {
            label: m.versus_tape_margin(),
            values: [summary.leads[0], summary.leads[1]] satisfies [
              number,
              number,
            ],
            text: [
              points(summary.leads[0]),
              points(summary.leads[1]),
            ] satisfies [string, string],
          },
        ]),
    {
      label: m.versus_tape_close(),
      values: [summary.close[0] ?? 0, summary.close[1] ?? 0] satisfies [
        number,
        number,
      ],
      text: [
        count(summary.close[0] ?? 0),
        count(summary.close[1] ?? 0),
      ] satisfies [string, string],
    },
    ...(summary.drift[0] == null || summary.drift[1] == null
      ? []
      : [
          {
            label: m.versus_tape_drift(),
            values: [summary.drift[0], summary.drift[1]] satisfies [
              number,
              number,
            ],
            text: [
              points(summary.drift[0]).replace("+", ""),
              points(summary.drift[1]).replace("+", ""),
            ] satisfies [string, string],
            isLowBetter: true,
          },
        ]),
    ...(questions.length === 0
      ? []
      : [
          {
            label: m.versus_tape_passed(),
            values: [passed[0] ?? 0, passed[1] ?? 0] satisfies [number, number],
            text: [count(passed[0] ?? 0), count(passed[1] ?? 0)] satisfies [
              string,
              string,
            ],
          },
        ]),
    {
      label: m.versus_tape_contests(),
      values: [summary.held[0] ?? 0, summary.held[1] ?? 0] satisfies [
        number,
        number,
      ],
      text: [
        count(summary.held[0] ?? 0),
        count(summary.held[1] ?? 0),
      ] satisfies [string, string],
    },
  ]);

  const cells = $derived(
    races.flatMap((race, row) => {
      const latest = new Map(race.rows.map((point) => [point.year, point]));
      return [...latest.values()].map((point) => {
        const half = (side: number) => {
          const won = point.winners[side];
          return won == null
            ? null
            : { color: tint(won.k), strength: Math.min(1, won.lead / 0.3) };
        };
        return {
          key: point.key,
          row,
          year: point.year,
          href: point.path,
          halves: [half(1), half(2)] satisfies [
            { color: string; strength: number } | null,
            { color: string; strength: number } | null,
          ],
          race,
          point,
        };
      });
    }),
  );

  const actives = $state<Record<number, string | null>>({});
</script>

{#snippet chip(won: (Entry & { lead: number }) | null, isNamed = false)}
  {#if won == null}
    <span class="text-muted-foreground">–</span>
  {:else}
    <span class="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span
        class="shrink-0 block-2 inline-2"
        style:background-color={tint(won.k)}
        aria-hidden="true"
      ></span>
      {const text = $derived(
        isNamed && won.who != null ? won.who : label(won.k),
      )}
      <span class="truncate max-inline-36" title={text}>{text}</span>
      <span class="font-figures font-semibold tabular-nums"
        >{points(won.lead)}</span
      >
    </span>
  {/if}
{/snippet}

{#snippet marker(side: number)}
  <span
    class={cn("inline-block shrink-0 bg-foreground block-2 inline-2", {
      "rounded-full": side === 0,
    })}
    aria-hidden="true"
  ></span>
{/snippet}

<section
  class="col-span-12 grid justify-items-center gap-4 bg-card px-3.5 pbs-4 pbe-5"
>
  {#if summary.total > 0}
    <p
      class="text-center text-lg/snug font-semibold text-balance max-inline-[60ch]"
    >
      {m.versus_verdict_places({
        a,
        b,
        same: count(summary.same),
        total: count(summary.total),
        share: percent(summary.same / summary.total, 0),
      })}
      {#if summary.split != null}
        <span class="text-muted-foreground"
          >{m.versus_verdict_split({
            year: String(summary.split.year),
            race: summary.split.race,
          })}</span
        >
      {/if}
    </p>
  {/if}
  <div class="inline-full max-inline-3xl">
    <Tape names={[a, b]} colors={[INK, INK]} rows={tape} />
  </div>
</section>

{#if races.length === 0}
  <p class="col-span-12 bg-card px-3.5 py-4 text-sm text-muted-foreground">
    {m.versus_places_none()}
  </p>
{:else}
  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-4 pbe-4"
    aria-labelledby="ledger"
  >
    <div class="grid gap-0.5 pe-10">
      <h2 id="ledger" class="heading">{m.versus_ledger_title()}</h2>
      <p class="text-xs text-pretty text-muted-foreground">
        {m.versus_ledger_places_key({ a, b })}
      </p>
    </div>
    <Ledger
      rows={races.map((race) => race.name)}
      {cells}
      label={m.versus_ledger_title()}
    >
      {#snippet readout(cell: (typeof cells)[number])}
        <div class="grid gap-1 text-[0.8125rem]">
          <p class="flex flex-wrap items-baseline gap-x-2">
            <span class="font-figures font-bold tabular-nums">{cell.year}</span>
            <span class="font-semibold">{cell.race.name}</span>
            {#if cell.point.path != null}
              <a
                class="truncate text-xs text-muted-foreground underline decoration-current/40 underline-offset-2 hover:text-foreground"
                href={resolve("/[...path]", { path: cell.point.path })}
                >{cell.point.name}</a
              >
            {/if}
          </p>
          <div
            class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 gap-y-0.5 sm:grid-cols-[auto_auto_auto_auto_minmax(0,1fr)]"
          >
            {#each [1, 2] as side (side)}
              <span class="flex items-center gap-1.5 font-semibold"
                >{@render marker(side - 1)}{names[side - 1]}</span
              >
              <span class="min-inline-0 sm:pe-4"
                >{@render chip(cell.point.winners[side] ?? null, true)}</span
              >
            {/each}
          </div>
        </div>
      {/snippet}
    </Ledger>
  </section>

  {#each races as race (race.index)}
    {const firstColor = $derived(tint(race.first))}
    {const secondColor = $derived(tint(race.second))}
    {const ordered = $derived(race.rows.toReversed())}
    <section
      class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-4 pbe-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:gap-x-6"
      aria-labelledby="race-{race.index}"
      data-export
    >
      <div class="grid content-start gap-3">
        <div class="relative grid gap-1 pe-10">
          <Export
            name="race-{race.index}"
            class="absolute inset-e-0 inset-bs-0"
          />
          <h2 id="race-{race.index}" class="heading">{race.name}</h2>
          <p class="text-xs text-pretty text-muted-foreground">
            {m.versus_duel_key({
              first: label(race.first),
              second: label(race.second),
              a,
              b,
            })}
            {#if race.kind === "combined"}
              {m.versus_combined_key()}
            {/if}
          </p>
          <p class="flex flex-wrap gap-x-4 gap-y-1 pbs-1 text-xs">
            {#each names as name, side (side)}
              <span class="flex items-center gap-1.5">
                <svg width="22" height="8" aria-hidden="true">
                  <line
                    x1="0"
                    x2="22"
                    y1="4"
                    y2="4"
                    class="stroke-foreground"
                    stroke-width="2"
                    stroke-dasharray={side === 0 ? null : "5 3"}
                  />
                </svg>{name}</span
              >
            {/each}
            {#if race.hasRef}
              <span class="flex items-center gap-1.5">
                <svg width="22" height="8" aria-hidden="true">
                  <line
                    x1="0"
                    x2="22"
                    y1="4"
                    y2="4"
                    class="stroke-muted-foreground"
                    stroke-width="1.5"
                    stroke-dasharray="1 3"
                    stroke-linecap="round"
                  />
                </svg>{m.versus_overall()}</span
              >
            {/if}
          </p>
        </div>
        <Duel
          points={race.rows.map((row) => ({
            key: row.key,
            x: row.x,
            a: row.margins[1] ?? null,
            b: row.margins[2] ?? null,
            ref: race.hasRef ? (row.margins[0] ?? null) : null,
            marks: [
              row.winners[1] == null ? null : tint(row.winners[1].k),
              row.winners[2] == null ? null : tint(row.winners[2].k),
            ],
          }))}
          colors={[INK, INK]}
          fills={[firstColor, secondColor]}
          bands={[firstColor, secondColor]}
          low={-race.top}
          high={race.top}
          format={(value: number) => String(Math.round(Math.abs(value) * 100))}
          bind:active={actives[race.index]}
        >
          {#snippet readout(point: { key: string })}
            {const row = $derived(
              race.rows.find((entry) => entry.key === point.key),
            )}
            {#if row != null}
              <p class="font-figures font-bold tabular-nums">{row.year}</p>
              <div
                class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2"
              >
                {#if race.hasRef}
                  <span class="text-muted-foreground">{m.versus_overall()}</span
                  >
                  {@render chip(row.winners[0] ?? null)}
                {/if}
                {#each [1, 2] as side (side)}
                  <span class="flex items-center gap-1.5"
                    >{@render marker(side - 1)}{names[side - 1]}</span
                  >
                  {@render chip(row.winners[side] ?? null)}
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
              {#if race.hasRef}
                <th class="pe-2 text-start font-semibold"
                  >{m.versus_overall()}</th
                >
              {/if}
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
              {const one = $derived(row.margins[1])}
              {const two = $derived(row.margins[2])}
              {const gap = $derived(
                one == null || two == null ? null : one - two,
              )}
              <tr
                class={cn("border-be border-border", {
                  highlighter: actives[race.index] === row.key,
                })}
                onpointerenter={() => (actives[race.index] = row.key)}
              >
                <td class="pe-2 font-figures tabular-nums block-7">
                  {#if row.path == null}
                    {row.year}
                  {:else}
                    <a
                      class="underline decoration-current/30 underline-offset-2 hover:decoration-current"
                      href={resolve("/[...path]", { path: row.path })}
                      >{row.year}</a
                    >
                  {/if}
                </td>
                {#if race.hasRef}
                  <td class="pe-2">{@render chip(row.winners[0] ?? null)}</td>
                {/if}
                <td class="pe-2">{@render chip(row.winners[1] ?? null)}</td>
                <td class="pe-2">{@render chip(row.winners[2] ?? null)}</td>
                <td class="text-end">
                  {#if gap == null}
                    <span class="text-muted-foreground">–</span>
                  {:else if Math.abs(gap) < 0.0005}
                    {m.versus_even()}
                  {:else}
                    <span
                      class="inline-flex items-center gap-1.5 font-figures tabular-nums"
                    >
                      <span
                        class="block-2 inline-2"
                        style:background-color={gap > 0
                          ? firstColor
                          : secondColor}
                        aria-hidden="true"
                      ></span>{points(Math.abs(gap))}</span
                    >
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>
  {/each}

  {#if questions.length > 0}
    <section
      class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-4 pbe-4"
      aria-labelledby="questions"
      data-export
    >
      <div class="grid gap-0.5 pe-10">
        <h2 id="questions" class="heading">{m.versus_questions()}</h2>
        <p class="text-xs text-pretty text-muted-foreground">
          {m.versus_questions_key()}
        </p>
      </div>
      <div
        class="relative overflow-x-auto overflow-y-hidden overscroll-x-contain min-inline-0 not-data-fits:overflow-y-auto not-data-fits:max-block-128 data-fits:overflow-visible"
        {@attach fits}
      >
        <table class="table-fixed text-[0.8125rem] inline-full">
          <colgroup>
            <col class="inline-16" />
            <col />
            <col class="inline-[40%] max-sm:inline-[34%]" />
            <col class="inline-24" />
          </colgroup>
          <thead
            class="sticky inset-bs-0 z-2 bg-background text-xs text-muted-foreground [&_th]:shadow-[inset_0_-1px_0_var(--color-border)]"
          >
            <tr>
              <th class="pe-2 text-start font-semibold block-7"
                >{m.versus_year()}</th
              >
              <th class="pe-2 text-start font-semibold">{m.col_contest()}</th>
              <th class="pe-2 font-semibold">
                <span class="flex justify-between font-figures tabular-nums"
                  ><span>0</span><span>50</span><span>100</span></span
                >
              </th>
              <th class="text-end font-semibold">
                <span class="flex justify-end gap-2">
                  {#each [0, 1] as side (side)}
                    <span class="flex items-center gap-1"
                      >{@render marker(side)}</span
                    >
                  {/each}
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {#each questions as point (point.key)}
              {const one = $derived(point.yes[1] ?? 0)}
              {const two = $derived(point.yes[2] ?? 0)}
              <tr class="border-be border-border">
                <td class="pe-2 font-figures tabular-nums block-8"
                  >{point.year}</td
                >
                <td class="truncate pe-2">
                  {#if point.path == null}
                    {point.name}
                  {:else}
                    <a
                      class="underline decoration-current/30 underline-offset-2 hover:decoration-current"
                      href={resolve("/[...path]", { path: point.path })}
                      title={point.name}>{point.name}</a
                    >
                  {/if}
                </td>
                <td class="pe-2">
                  <span class="relative block block-4" aria-hidden="true">
                    <span
                      class="absolute inset-x-0 inset-bs-1/2 border-bs border-border"
                    ></span>
                    <span
                      class="absolute inset-y-0 inset-s-1/2 border-s border-muted-foreground/50"
                    ></span>
                    <span
                      class="absolute inset-bs-1/2 -translate-y-1/2 bg-foreground/50 block-0.5"
                      style:inset-inline-start="{Math.min(one, two) * 100}%"
                      style:inline-size="{Math.abs(one - two) * 100}%"
                    ></span>
                    <span
                      class="absolute inset-bs-1/2 -translate-1/2 border border-background block-2.5 inline-2.5"
                      style:inset-inline-start="{two * 100}%"
                      style:background-color={two > 0.5
                        ? "var(--yes)"
                        : "var(--no)"}
                    ></span>
                    <span
                      class="absolute inset-bs-1/2 -translate-1/2 rounded-full border border-background block-2.5 inline-2.5"
                      style:inset-inline-start="{one * 100}%"
                      style:background-color={one > 0.5
                        ? "var(--yes)"
                        : "var(--no)"}
                    ></span>
                  </span>
                </td>
                <td
                  class="text-end font-figures whitespace-nowrap tabular-nums"
                >
                  {percent(one, 0)}
                  <span class="text-muted-foreground">{percent(two, 0)}</span>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <Export name="questions" class="absolute inset-e-2 inset-bs-2" />
    </section>
  {/if}
{/if}
