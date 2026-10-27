<script lang="ts">
  import { enhance } from "$app/forms";
  import { invalidateAll } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import type { Column, Counts, Goal, Reason, Tier } from "#lib/coverage.js";
  import Bar from "#routes/coverage/bar.svelte";
  import Gaps from "#routes/coverage/gaps.svelte";
  import {
    COLUMN_LABELS,
    COUNTRY_COLUMNS,
    grains,
    headline,
    label,
    levelLabel,
    measures,
    REASONS,
    spans,
    TIER_LABELS,
    US_COLUMNS,
    ERA_LABELS,
    columnGroup,
    weighted,
  } from "#lib/coverage.js";
  import Tone from "#routes/coverage/tone.svelte";

  const { data }: { data: PageData } = $props();

  const DEFINITIONS: Record<string, () => string> = {
    federal: m.coverage_tier_def_federal,
    statewide: m.coverage_tier_def_statewide,
    legislative: m.coverage_tier_def_legislative,
    local: m.coverage_tier_def_local,
    turnout: m.coverage_tier_def_turnout,
    national: m.coverage_tier_def_national,
    subnational: m.coverage_tier_def_subnational,
    referendums: m.coverage_tier_def_referendums,
    ep: m.coverage_tier_def_ep,
  };
  const US_DEFINITIONS: Record<string, () => string> = {
    local: m.coverage_tier_def_local_us,
    referendums: m.coverage_tier_def_referendums_us,
  };
  const COMPLETE: Partial<Record<Goal, () => string>> = {
    county: m.coverage_reason_complete_county_def,
    history: m.coverage_reason_complete_history_def,
    turnout: m.coverage_reason_complete_turnout_def,
    precinct: m.coverage_reason_complete_precinct_def,
    precinct_shapes: m.coverage_reason_precinct_shaped_def,
    shapes: m.coverage_reason_complete_shapes_def,
    shapes_history: m.coverage_reason_complete_shapes_def,
    local: m.coverage_reason_complete_local_def,
  };
  const COUNTRY_COMPLETE: Partial<Record<Goal, () => string>> = {
    national: m.coverage_country_def_national,
    fine: m.coverage_country_def_fine,
    shapes: m.coverage_country_def_shapes,
    subnational: m.coverage_country_def_subnational,
    local: m.coverage_country_def_local,
    referendums: m.coverage_country_def_referendums,
    ep: m.coverage_country_def_ep,
  };
  const US_REASONS: Partial<Record<Reason, () => string>> = {
    pending: m.coverage_reason_pending_def,
    major: m.coverage_reason_major_def,
    coarse: m.coverage_reason_coarse_def,
    partial: m.coverage_reason_partial_def,
    totals: m.coverage_reason_totals_def,
    bare: m.coverage_reason_bare_def,
    absent: m.coverage_reason_absent_def,
    units: m.coverage_reason_units_def,
    unshaped: m.coverage_reason_unshaped_def,
    undrawn: m.coverage_reason_undrawn_def,
  };
  const TURNOUT_REASONS: Partial<Record<Reason, () => string>> = {
    partial: m.coverage_reason_turnout_partial_def,
    coarse: m.coverage_reason_turnout_coarse_def,
    absent: m.coverage_reason_turnout_absent_def,
  };
  const READY_LEVELS = [
    "us_house",
    "us_senate",
    "governor",
    "statewide_exec",
    "state_upper",
    "state_lower",
    "statewide_measure",
  ];
  const READY = [
    {
      reason: "complete",
      label: m.coverage_ready_complete,
      definition: m.coverage_ready_complete_def,
    },
    {
      reason: "partial",
      label: m.coverage_ready_partial,
      definition: m.coverage_ready_partial_def,
    },
    {
      reason: "bare",
      label: m.coverage_ready_bare,
      definition: m.coverage_ready_bare_def,
    },
    {
      reason: "absent",
      label: m.coverage_ready_absent,
      definition: m.coverage_ready_absent_def,
    },
  ] as const;
  const COUNTRY_REASONS: Partial<Record<Reason, () => string>> = {
    pending: m.coverage_reason_pending_def,
    coarse: m.coverage_country_reason_coarse_def,
    partial: m.coverage_country_reason_partial_def,
    totals: m.coverage_country_reason_totals_def,
    bare: m.coverage_country_reason_bare_def,
    lost: m.coverage_country_reason_lost_def,
    absent: m.coverage_country_reason_absent_def,
    unshaped: m.coverage_country_reason_unshaped_def,
    undrawn: m.coverage_country_reason_undrawn_def,
  };
  const GAP_LABELS: Partial<Record<Goal, () => string>> = {
    county: m.coverage_gap_county,
    precinct: m.coverage_gap_precinct,
    precinct_shapes: m.coverage_gap_precinct_shapes,
    history: m.coverage_gap_history,
    shapes_history: m.coverage_gap_shapes_history,
    turnout: m.coverage_gap_turnout,
    fine: m.coverage_gap_fine,
  };

  const year = String(new Date().getFullYear());
  const isUs = $derived(data.polity === "us");
  const summary = $derived(data.summary);
  const columns = $derived<ReadonlyArray<Column>>(
    isUs
      ? US_COLUMNS
      : COUNTRY_COLUMNS.filter((column) =>
          data.tiers.some((row) => row.cells[column] != null),
        ),
  );
  const opens = (used: ReadonlyArray<Column>, index: number) => {
    const previous = used[index - 1];
    const column = used[index];
    return (
      previous == null ||
      column == null ||
      columnGroup(column) !== columnGroup(previous)
    );
  };
  const slot = (column: Column, tier: string): Column =>
    tier === "turnout" && column === "before_any"
      ? "before"
      : tier === "turnout" && column === "since_any"
        ? "since"
        : column;
  const notesFor = (level: string) =>
    summary.notes.filter(
      (note) =>
        note.level === level &&
        note.goal !== "fine" &&
        note.lost == null &&
        note.canvass == null &&
        note.readiness == null,
    );

  const range = $derived.by(() => {
    const years = [
      summary.first ?? Number(year),
      summary.last ?? Number(year),
      ...data.tiers.flatMap((tier) =>
        tier.levels.flatMap((level) => level.years),
      ),
      ...summary.notes.flatMap((note) =>
        [note.from, note.to].filter((value) => value != null),
      ),
    ];
    const from = Math.min(...years);
    return { from, to: Math.max(...years, from + 1) };
  });
  const marks = $derived.by(() => {
    const step = range.to - range.from > 120 ? 50 : 25;
    const found: Array<number> = [];
    for (
      let at = Math.ceil((range.from + 1) / step) * step;
      at < range.to;
      at += step
    ) {
      const share = (at - range.from) / (range.to - range.from + 1);
      if (share > 0.15 && share < 0.8) {
        found.push(at);
      }
    }
    return found;
  });
  const at = (value: number) =>
    ((value - range.from) / (range.to - range.from + 1)) * 100;

  let picked = $state<string | null>(null);
  const tab = $derived(picked ?? data.tiers[0]?.key ?? "");
  let grain = $state<Goal | null>(null);
  const tier = $derived(data.tiers.find((found) => found.key === tab));
  const options = $derived(
    grains(data.polity, tab).filter((goal) => {
      if (tier == null) {
        return false;
      }
      if (!isUs) {
        const column = goal === tab ? "constituency" : goal;
        return (tier.cells[column]?.total ?? 0) > 0;
      }
      return true;
    }),
  );
  const goal = $derived(
    grain != null && options.includes(grain)
      ? grain
      : (options[0] ?? headline(data.polity, tab).goal),
  );
  let order = $state<"gap" | "name">("gap");

  $effect(() => {
    if (!data.building) {
      return;
    }
    const timer = setInterval(() => void invalidateAll(), 5000);
    return () => clearInterval(timer);
  });

  const built = $derived(
    data.builtAt == null
      ? m.coverage_building()
      : m.coverage_built({
          time: new Intl.DateTimeFormat(getLocale(), {
            dateStyle: "medium",
            timeStyle: "short",
          }).format(new Date(data.builtAt)),
        }),
  );
  const title = $derived(
    isUs
      ? m.coverage_title()
      : m.coverage_country_title({ name: summary.name }),
  );
</script>

<svelte:head>
  <title>{title} | {m.wordmark()}</title>
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <meta name="robots" content="noindex" />
</svelte:head>

{#snippet cell(counts: Counts | null | undefined, isLocal: boolean)}
  {#if counts == null}
    <span class="text-muted-foreground" title={m.coverage_cell_na()}
      ><span aria-hidden="true">–</span><span class="sr-only"
        >{m.coverage_cell_na()}</span
      ></span
    >
  {:else if counts.total === 0}
    <span class="text-xs text-muted-foreground">{m.coverage_cell_none()}</span>
  {:else}
    <span class="grid gap-0.5 tabular-nums">
      <span class="font-semibold"
        >{percent(counts.complete / counts.total, 1)}</span
      >
      <span
        aria-hidden="true"
        class="flex overflow-hidden rounded-xs bg-muted block-1 max-inline-24"
      >
        <span
          class="block-full"
          style:inline-size="{(counts.complete / counts.total) * 100}%"
        >
          <Tone reason="complete" class="block block-full inline-full" />
        </span>
      </span>
      <span class="text-xs text-muted-foreground"
        >{m.coverage_cell_of({
          complete: count(counts.complete),
          total: count(counts.total),
        })}</span
      >
      {#if isLocal && counts.populationTotal > 0}
        <span class="text-xs text-muted-foreground"
          >{m.coverage_population_share({
            percent: percent(
              counts.populationComplete / counts.populationTotal,
              1,
            ),
          })}</span
        >
      {/if}
    </span>
  {/if}
{/snippet}

{#snippet strip(
  name: string,
  years: Array<number>,
  gaps: Array<{ from?: number | null; to?: number | null }>,
  counted: string,
)}
  {const uncounted = $derived(
    gaps
      .map((note) =>
        note.from === note.to ? String(note.from) : `${note.from}–${note.to}`,
      )
      .join(", "),
  )}
  <span
    role="img"
    aria-label={uncounted === ""
      ? m.coverage_ledger_strip_counted({ label: name, counted })
      : m.coverage_ledger_strip({
          label: name,
          counted: counted === "" ? m.coverage_ledger_nothing() : counted,
          uncounted,
        })}
    class="relative block overflow-hidden border-be border-border block-4 min-inline-56"
  >
    {#each gaps as note, index (index)}
      <span
        class="absolute inset-be-0 border border-be-0 border-muted-foreground/60 bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_4px)] block-2.5"
        style:inset-inline-start="{at(note.from ?? range.from)}%"
        style:inline-size="{at((note.to ?? range.to) + 1) -
          at(note.from ?? range.from)}%"
      ></span>
    {/each}
    {#each years as value (value)}
      <span
        class="absolute inset-be-0 bg-foreground block-full inline-0.5"
        style:inset-inline-start="{at(value)}%"
      ></span>
    {/each}
  </span>
{/snippet}

{#snippet axis()}
  <span
    aria-hidden="true"
    class="relative block font-normal text-muted-foreground tabular-nums block-4 min-inline-56"
  >
    <span class="absolute inset-s-0">{range.from}</span>
    {#each marks as mark (mark)}
      <span
        class="absolute -translate-x-1/2"
        style:inset-inline-start="{at(mark)}%">{mark}</span
      >
    {/each}
    <span class="absolute inset-e-0">{range.to}</span>
  </span>
{/snippet}

{#snippet head(isKinds: boolean, used: ReadonlyArray<Column>)}
  {const runs = $derived(
    used.flatMap((column, index) => {
      if (!opens(used, index)) {
        return [];
      }
      let span = 1;
      while (index + span < used.length && !opens(used, index + span)) {
        span += 1;
      }
      return [{ column, span }];
    }),
  )}
  <thead class="text-xs/tight">
    {#if isUs}
      <tr>
        <th
          scope="col"
          rowspan="2"
          class="px-3.5 py-1.5 text-start align-bottom"
          >{m.coverage_column_kind()}</th
        >
        {#each runs as run (run.column)}
          <th
            scope="colgroup"
            colspan={run.span}
            class="border-s px-3 pbs-1.5 text-start text-muted-foreground"
            >{columnGroup(run.column)()}</th
          >
        {/each}
      </tr>
      <tr>
        {#each used as column, index (column)}
          <th
            scope="col"
            class={cn("px-3 pbe-1.5 text-start align-bottom", {
              "border-s": opens(used, index),
            })}>{COLUMN_LABELS[column]()}</th
          >
        {/each}
      </tr>
    {:else}
      <tr>
        <th scope="col" class="px-3.5 py-1.5 text-start align-bottom"
          >{m.coverage_column_kind()}</th
        >
        {#if isKinds}
          <th scope="col" class="px-3 py-1.5 text-start align-bottom"
            ><span class="grid gap-1"
              >{m.coverage_column_years()}{@render axis()}</span
            ></th
          >
        {/if}
        {#each used as column (column)}
          <th scope="col" class="px-3 py-1.5 text-start align-bottom"
            >{COLUMN_LABELS[column]()}</th
          >
        {/each}
      </tr>
    {/if}
  </thead>
{/snippet}

{#snippet cards(cells: Record<string, Counts | null>, tierKey: string)}
  <dl class="grid grid-cols-2 gap-x-4 gap-y-2.5 text-sm">
    {#each columns as column (column)}
      {const value = $derived(cells[slot(column, tierKey)])}
      {#if value !== undefined}
        <div class="grid content-start gap-0.5">
          <dt class="text-xs text-muted-foreground">
            {isUs
              ? `${columnGroup(column)()}, ${COLUMN_LABELS[column]().toLowerCase()}`
              : COLUMN_LABELS[column]()}
          </dt>
          <dd>{@render cell(value, tierKey === "local" && isUs)}</dd>
        </div>
      {/if}
    {/each}
  </dl>
{/snippet}

{#snippet legend(
  counts: Counts,
  shown: Counts,
  goal: Goal,
  isWeighted: boolean,
  tierKey: string,
)}
  <ul class="grid gap-y-2 text-sm">
    {#each REASONS[goal].filter((reason) => reason === "complete" || shown[reason] > 0) as reason (reason)}
      {const definition = $derived(
        (reason === "complete"
          ? isUs && goal === "history" && tierKey === "legislative"
            ? m.coverage_reason_complete_district_def
            : (isUs ? COMPLETE : COUNTRY_COMPLETE)[goal]
          : !isUs
            ? COUNTRY_REASONS[reason]
            : reason === "absent" && goal === "local"
              ? m.coverage_reason_absent_local_def
              : goal === "turnout"
                ? TURNOUT_REASONS[reason]
                : goal === "precinct_shapes" && reason === "unshaped"
                  ? m.coverage_reason_precinct_unshaped_def
                  : US_REASONS[reason])?.() ?? "",
      )}
      {const span = $derived(
        isUs && reason === "unshaped"
          ? data.unshaped.find(
              (found) => found.tier === tierKey && found.goal === goal,
            )
          : undefined,
      )}
      <li class="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-x-2">
        <Tone {reason} class="mbs-1 rounded-xs block-3 inline-3" />
        <span>
          <span class="font-semibold">{label(goal, reason, data.polity)}</span>
          <span class="tabular-nums"
            >{isWeighted
              ? m.coverage_population_share({
                  percent: percent(shown[reason] / shown.total, 1),
                })
              : count(counts[reason])}</span
          >
          <span class="block text-xs text-muted-foreground"
            >{definition}{#if span != null}{" "}{(span.from >= 2000 &&
                span.to <= 2002
                ? m.coverage_unshaped_span_redraw
                : m.coverage_unshaped_span)({
                years:
                  span.from === span.to
                    ? String(span.from)
                    : `${span.from}–${span.to}`,
              })}{/if}</span
          >
          {#if reason === "complete" && counts.upcoming > 0}
            <span class="mbs-2 grid grid-cols-[0.75rem_minmax(0,1fr)] gap-x-2">
              <Tone
                reason="upcoming"
                class="mbs-1 rounded-xs block-3 inline-3"
              />
              <span>
                <span class="font-semibold">{m.coverage_upcoming()}</span>
                <span class="tabular-nums">{count(counts.upcoming)}</span>
                <span class="block text-xs text-muted-foreground"
                  >{m.coverage_upcoming_def()}</span
                >
              </span>
            </span>
          {/if}
          {#if reason === "complete" && counts.icpsr > 0}
            <span class="mbs-2 grid grid-cols-[0.75rem_minmax(0,1fr)] gap-x-2">
              <Tone reason="icpsr" class="mbs-1 rounded-xs block-3 inline-3" />
              <span>
                <span class="font-semibold">{m.coverage_icpsr()}</span>
                <span class="tabular-nums">{count(counts.icpsr)}</span>
                <span class="block text-xs text-muted-foreground"
                  >{m.coverage_icpsr_def()}</span
                >
              </span>
            </span>
          {/if}
          {#if reason === "complete" && counts.published > 0}
            <span class="mbs-2 grid grid-cols-[0.75rem_minmax(0,1fr)] gap-x-2">
              <Tone
                reason="published"
                class="mbs-1 rounded-xs block-3 inline-3"
              />
              <span>
                <span class="font-semibold">{m.coverage_published()}</span>
                <span class="tabular-nums">{count(counts.published)}</span>
                <span class="block text-xs text-muted-foreground"
                  >{m.coverage_published_def()}</span
                >
              </span>
            </span>
          {/if}
          {#if reason === "complete" && goal === "precinct"}
            {const levels = $derived(
              data.tiers
                .find((tier) => tier.key === tierKey)
                ?.levels.map((level) => level.key) ?? [],
            )}
            {const fixes = $derived(
              summary.notes.filter(
                (note) => note.canvass != null && levels.includes(note.level),
              ),
            )}
            {#if fixes.length > 0}
              <span class="mbs-2 grid gap-1">
                <span class="font-semibold">{m.coverage_canvass()}</span>
                <span class="text-xs text-muted-foreground"
                  >{m.coverage_canvass_def()}</span
                >
                {#each fixes as note, index (index)}
                  <span class="text-xs">
                    <a
                      href={note.url}
                      class="underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"
                      >{m.coverage_canvass_row({
                        state: note.canvass ?? "",
                        year: String(note.from),
                        office: levelLabel(data.polity, note.level),
                      })}</a
                    >
                    <span class="block text-muted-foreground">{note.note}</span>
                  </span>
                {/each}
              </span>
            {/if}
          {/if}
          {#if reason === "lost"}
            {const levels = $derived(
              data.tiers
                .find((tier) => tier.key === tierKey)
                ?.levels.map((level) => level.key) ?? [],
            )}
            <span class="mbs-1 grid gap-1 text-xs">
              {#each summary.notes.filter((note) => note.lost != null && levels.includes(note.level)) as note, index (index)}
                {const source = $derived(summary.sources[note.lost ?? ""])}
                <span>
                  <a
                    href={source?.url}
                    class="underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"
                    >{m.coverage_lost_span({
                      years:
                        note.from === note.to
                          ? String(note.from)
                          : `${note.from}–${note.to}`,
                      source: source?.title ?? note.lost ?? "",
                    })}</a
                  >
                  <span class="block text-muted-foreground">{note.note}</span>
                </span>
              {/each}
            </span>
          {/if}
        </span>
      </li>
    {/each}
  </ul>
{/snippet}

{#snippet measure(
  counts: Counts,
  goal: Goal,
  title: string,
  progress: (inputs: { complete: string; total: string }) => string,
  tierKey: string,
  isBonus: boolean,
)}
  {const isWeighted = $derived(goal === "local" && counts.populationTotal > 0)}
  {const shown = $derived(
    isWeighted
      ? weighted(counts)
      : isBonus
        ? { ...counts, unshaped: 0 }
        : counts,
  )}
  {const held = $derived(counts.total - counts.absent)}
  <div class="grid content-start gap-2 min-inline-0">
    <p class="text-xs font-bold tracking-wide text-muted-foreground uppercase">
      {title}
    </p>
    {#if isWeighted}
      <p class="text-lg font-bold tracking-[-0.01em] tabular-nums">
        {m.coverage_progress_local_population({
          percent: percent(
            counts.populationComplete / counts.populationTotal,
            1,
          ),
        })}
      </p>
    {/if}
    <p
      class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-sm tabular-nums"
    >
      <span class={cn({ "text-lg font-bold tracking-[-0.01em]": !isWeighted })}>
        {(isWeighted ? m.coverage_progress_local_count : progress)({
          complete: count(counts.complete),
          total: count(counts.total),
        })}
      </span>
      <span class="text-muted-foreground">
        {m.coverage_percent({
          percent: percent(counts.complete / Math.max(1, counts.total), 1),
        })}
      </span>
      {#if counts.upcoming > 0}
        <span class="text-muted-foreground">
          {m.coverage_upcoming_of({ count: count(counts.upcoming) })}
        </span>
      {/if}
      {#if counts.icpsr > 0}
        <span class="text-muted-foreground">
          {m.coverage_icpsr_of({ count: count(counts.icpsr) })}
        </span>
      {/if}
      {#if counts.published > 0}
        <span class="text-muted-foreground">
          {m.coverage_published_of({ count: count(counts.published) })}
        </span>
      {/if}
      {#if counts.lost > 0}
        <span class="text-muted-foreground">
          {m.coverage_lost_of({ count: count(counts.lost) })}
        </span>
      {/if}
      {#if !isBonus}
        <span class="text-muted-foreground">
          {m.coverage_short({
            count: count(counts.total - counts.complete - counts.lost),
          })}
        </span>
      {/if}
    </p>
    <Bar
      counts={shown}
      {goal}
      polity={data.polity}
      population={isWeighted}
      class="block-3"
    />
    <div class="hidden pbs-1 md:block">
      {@render legend(counts, shown, goal, isWeighted, tierKey)}
    </div>
    <details class="group md:hidden">
      <summary
        class="flex cursor-pointer list-none items-center gap-1 text-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden"
      >
        <ChevronRightIcon
          class="transition-transform block-4 inline-4 group-open:rotate-90"
        />
        {m.coverage_legend()}
      </summary>
      <div class="pbs-2">
        {@render legend(counts, shown, goal, isWeighted, tierKey)}
      </div>
    </details>
    {#if isUs && goal === "history" && tierKey !== "legislative"}
      <p class="text-xs text-muted-foreground">
        {m.coverage_note_history_1824()}
      </p>
    {:else if goal === "county" && held > 0}
      <p class="text-xs text-muted-foreground">
        {m.coverage_turnout({
          count: count(counts.turnout),
          held: count(held),
        })}
      </p>
    {:else if goal === "precinct_shapes"}
      <p class="text-xs text-muted-foreground max-inline-[72ch]">
        {(isBonus
          ? m.coverage_note_precinct_shapes_bonus
          : m.coverage_note_precinct_shapes)()}
      </p>
    {:else if isUs && (goal === "shapes" || goal === "shapes_history")}
      <p class="text-xs text-muted-foreground">
        {m.coverage_note_shapes()}
      </p>
    {/if}
    {#if isWeighted}
      <p class="text-xs text-muted-foreground max-inline-[72ch]">
        {m.coverage_local_two_measures()}
        {m.coverage_local_weights()}
        {m.coverage_local_estimated({
          percent: percent(
            counts.populationEstimated / counts.populationTotal,
            1,
          ),
        })}
      </p>
    {/if}
  </div>
{/snippet}

<main class="pbe-16">
  <article class="grid gap-px overflow-clip border-be bg-border">
    <header class="grid gap-2 bg-card px-3.5 pbs-4 pbe-3.5">
      <a
        href={resolve("/coverage")}
        class="text-sm text-muted-foreground underline-offset-2 inline-fit hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
        >{m.coverage_country_back()}</a
      >
      <h1 class="title">{title}</h1>
      <p class="text-[0.9375rem] text-muted-foreground max-inline-[72ch]">
        {(isUs ? m.coverage_lede : m.coverage_country_lede)()}
      </p>
      {#if summary.first != null && summary.last != null}
        <p class="text-[0.9375rem] tabular-nums max-inline-[72ch]">
          {m.coverage_country_facts({
            elections: count(summary.elections),
            first: String(summary.first),
            last: String(summary.last),
          })}
          {#if summary.finestNoun != null}
            {m.coverage_country_finest({ noun: summary.finestNoun })}
          {/if}
        </p>
      {/if}
      <p class="text-xs text-muted-foreground max-inline-[72ch]">
        {m.coverage_upcoming_note()}
      </p>
      <div
        class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"
      >
        {built}
        {#if data.dev}
          <form method="POST" action="?/refresh" use:enhance>
            <button
              type="submit"
              disabled={data.building}
              class="rounded-sm border border-border px-2 whitespace-nowrap text-foreground block-6 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50 disabled:hover:bg-transparent"
              >{m.coverage_refresh()}</button
            >
          </form>
          {#if data.building}
            <span>{m.coverage_refresh_requested()}</span>
          {/if}
        {/if}
      </div>
      {#if data.stale}
        <p role="status" class="text-sm font-semibold text-(--no)">
          {m.coverage_stale()}
        </p>
      {/if}
    </header>

    {#if data.readiness.length > 0}
      <section
        aria-labelledby="coverage-ready"
        class="grid gap-4 bg-card px-3.5 pbs-4 pbe-4"
      >
        <hgroup class="grid gap-1">
          <h2 id="coverage-ready" class="text-lg font-bold">
            {m.coverage_ready_title()}
          </h2>
          <p class="text-sm text-muted-foreground max-inline-[80ch]">
            {m.coverage_ready_lede()}
          </p>
        </hgroup>
        <ul class="grid gap-x-6 gap-y-5 sm:grid-cols-2 lg:grid-cols-4">
          {#each READY_LEVELS.flatMap( (level) => data.readiness.filter((row) => row.level === level) ) as row (row.level)}
            {const short = $derived(
              row.states.filter((state) => state.complete < state.total),
            )}
            <li class="grid content-start gap-1">
              <span class="text-sm font-semibold"
                >{levelLabel(data.polity, row.level)}</span
              >
              <span class="text-lg font-bold tracking-[-0.01em] tabular-nums"
                >{percent(row.complete / Math.max(1, row.total), 1)}</span
              >
              <span
                role="img"
                aria-label={READY.filter((part) => row[part.reason] > 0)
                  .map((part) =>
                    m.coverage_count({
                      label: part.label(),
                      count: count(row[part.reason]),
                    }),
                  )
                  .join(", ")}
                class="flex overflow-hidden rounded-xs bg-muted block-2.5 max-inline-56"
              >
                {#each READY.filter((part) => row[part.reason] > 0) as part (part.reason)}
                  <span
                    class="block-full"
                    style:inline-size="{(row[part.reason] /
                      Math.max(1, row.total)) *
                      100}%"
                  >
                    <Tone
                      reason={part.reason}
                      class="block block-full inline-full"
                    />
                  </span>
                {/each}
              </span>
              <span class="text-xs text-muted-foreground tabular-nums"
                >{m.coverage_ready_of({
                  complete: count(row.complete),
                  total: count(row.total),
                })}</span
              >
              {#if short.length > 0}
                <details class="group text-xs">
                  <summary
                    class="flex cursor-pointer list-none items-center gap-1 focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden"
                  >
                    <ChevronRightIcon
                      class="text-muted-foreground transition-transform block-3.5 inline-3.5 group-open:rotate-90"
                    />
                    {m.coverage_ready_states()} ({count(short.length)})
                  </summary>
                  <ul
                    class="grid grid-cols-[repeat(auto-fill,minmax(5.5rem,1fr))] gap-x-3 gap-y-0.5 pbs-1.5 tabular-nums"
                  >
                    {#each short as state (state.state)}
                      <li>
                        <span class="font-semibold">{state.state}</span>
                        {count(state.complete)}/{count(state.total)}
                      </li>
                    {/each}
                  </ul>
                </details>
              {/if}
            </li>
          {/each}
        </ul>
        <ul class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {#each READY as part (part.reason)}
            <li class="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-x-2">
              <Tone
                reason={part.reason}
                class="mbs-1 rounded-xs block-3 inline-3"
              />
              <span>
                <span class="font-semibold">{part.label()}</span>
                <span class="block text-xs text-muted-foreground"
                  >{part.definition()}</span
                >
              </span>
            </li>
          {/each}
        </ul>
        <div class="grid gap-1 text-xs text-muted-foreground">
          <p>{m.coverage_ready_schedule()}</p>
          <p class="font-semibold text-foreground">
            {m.coverage_ready_sources()}
          </p>
          <ul class="grid gap-0.5">
            {#each summary.notes.filter((note) => note.readiness != null) as note, index (index)}
              {const source = $derived(summary.sources[note.readiness ?? ""])}
              <li>
                <span class="text-foreground"
                  >{levelLabel(data.polity, note.level)}:</span
                >
                <a
                  href={source?.url}
                  class="underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"
                  >{source == null
                    ? note.readiness
                    : `${source.publisher ?? ""}, ${source.title}`}</a
                >
              </li>
            {/each}
          </ul>
        </div>
      </section>
    {/if}

    <section aria-labelledby="coverage-glance" class="grid gap-3 bg-card pbs-4">
      <hgroup class="grid gap-1 px-3.5">
        <h2 id="coverage-glance" class="text-lg font-bold">
          {m.coverage_glance()}
        </h2>
        <p class="text-sm text-muted-foreground max-inline-[80ch]">
          {(isUs
            ? m.coverage_glance_hint_us
            : m.coverage_glance_hint_country)()}
          {m.coverage_glance_none_hint()}
        </p>
      </hgroup>
      <table class="hidden text-sm md:table">
        {@render head(false, columns)}
        <tbody>
          {#each data.tiers as row (row.key)}
            <tr class="border-bs align-top">
              <th scope="row" class="px-3.5 py-2.5 text-start font-semibold">
                <a
                  href="#tier-{row.key}"
                  class="underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                  >{TIER_LABELS[row.key as Tier]()}</a
                >
              </th>
              {#each columns as column, index (column)}
                <td
                  class={cn("px-3 py-2.5", {
                    "border-s": isUs && opens(columns, index),
                  })}
                >
                  {@render cell(
                    row.cells[slot(column, row.key)],
                    isUs && row.key === "local",
                  )}
                </td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
      <ul class="grid md:hidden">
        {#each data.tiers as row (row.key)}
          <li class="grid gap-2 border-bs px-3.5 py-3">
            <a
              href="#tier-{row.key}"
              class="font-semibold underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
              >{TIER_LABELS[row.key as Tier]()}</a
            >
            {@render cards(row.cells, row.key)}
          </li>
        {/each}
      </ul>
    </section>

    {#each data.tiers as row (row.key)}
      {const lead = $derived(headline(data.polity, row.key))}
      {const used = $derived(
        columns.filter((column) => slot(column, row.key) in row.cells),
      )}
      {const blocks = $derived(
        measures(data.polity, row.key).flatMap((block) => {
          const items = block.items.flatMap((item) => {
            const found = row.cells[item.column];
            return found == null || found.total === 0
              ? []
              : [{ ...item, counts: found }];
          });
          return items.length === 0 ? [] : [{ era: block.era, items }];
        }),
      )}
      <section
        id="tier-{row.key}"
        aria-labelledby="tier-{row.key}-title"
        class="grid scroll-mt-16 gap-4 bg-card px-3.5 py-4"
      >
        <hgroup class="grid gap-1">
          <h2 id="tier-{row.key}-title" class="text-lg font-bold">
            {TIER_LABELS[row.key as Tier]()}
          </h2>
          <p class="text-sm text-muted-foreground max-inline-[72ch]">
            {(
              (isUs ? US_DEFINITIONS[row.key] : undefined) ??
              DEFINITIONS[row.key]
            )?.()}
          </p>
          {#if isUs && (row.key === "local" || row.key === "turnout")}
            <p class="text-sm text-muted-foreground max-inline-[72ch]">
              {(row.key === "local"
                ? m.coverage_goal_local_note
                : m.coverage_goal_turnout_note)()}
            </p>
          {/if}
        </hgroup>
        {#if blocks.length > 0}
          <div class="grid gap-5">
            {#each blocks as block (block.era)}
              <div class="grid gap-3">
                {#if block.era != null}
                  <h3 class="text-sm font-bold">
                    {ERA_LABELS[block.era]()}
                  </h3>
                {/if}
                <div class="grid gap-x-8 gap-y-5 lg:grid-cols-2 xl:grid-cols-3">
                  {#each block.items as item (item.column)}
                    {@render measure(
                      item.counts,
                      item.goal,
                      item.title(),
                      isUs
                        ? item.goal === "local"
                          ? m.coverage_progress_local
                          : item.goal === "turnout"
                            ? m.coverage_progress_turnout
                            : item.goal === "precinct_shapes"
                              ? m.coverage_progress_precincts
                              : m.coverage_progress
                        : lead.progress,
                      row.key,
                      item.column === "precinct_shapes_2000",
                    )}
                  {/each}
                </div>
              </div>
            {/each}
          </div>
        {:else}
          <p class="text-sm text-muted-foreground">
            {m.coverage_country_none()}
          </p>
        {/if}
        <div class="grid gap-2">
          <div
            class="hidden flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground md:flex"
          >
            <h3 class="text-sm font-bold text-foreground">
              {m.coverage_ledger()}
            </h3>
            {#if !isUs}
              <span class="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  class="bg-foreground block-3 inline-0.5"
                ></span>
                {m.coverage_ledger_counted()}
              </span>
              <span class="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  class="border border-muted-foreground/60 bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_4px)] block-2.5 inline-4"
                ></span>
                {m.coverage_ledger_uncounted()}
              </span>
            {/if}
          </div>
          <table class="hidden text-sm md:table">
            {@render head(!isUs, used)}
            <tbody>
              {#each row.levels as level (level.key)}
                {const notes = $derived(notesFor(level.key))}
                {const gaps = $derived(
                  notes.filter(
                    (note) => note.from != null && note.listed !== false,
                  ),
                )}
                {const unlisted = $derived(
                  notes.filter(
                    (note) => note.listed === false && note.from != null,
                  ),
                )}
                {const missing = $derived(
                  gaps
                    .map((note) =>
                      note.from === note.to
                        ? String(note.from)
                        : `${note.from}–${note.to}`,
                    )
                    .join(", "),
                )}
                {const remark = $derived(
                  summary.levels.find((found) => found.key === level.key)?.note,
                )}
                {const general = $derived([
                  ...(remark == null ? [] : [remark]),
                  ...notes.flatMap((note) =>
                    note.from == null ? [note.note] : [],
                  ),
                ])}
                {const counted = $derived(
                  spans(
                    level.years,
                    gaps.flatMap((note) =>
                      note.from == null ? [] : [note.from],
                    ),
                  ),
                )}
                <tr class="border-bs align-top">
                  <th
                    scope="row"
                    class="px-3.5 py-2.5 text-start font-semibold"
                  >
                    {levelLabel(data.polity, level.key)}
                  </th>
                  {#if !isUs}
                    <td class="px-3 py-2.5 tabular-nums"
                      ><span class="grid gap-1.5"
                        >{@render strip(
                          levelLabel(data.polity, level.key),
                          level.years,
                          gaps,
                          counted,
                        )}<span
                          >{counted === ""
                            ? m.coverage_ledger_nothing()
                            : counted}</span
                        >{#if missing !== ""}<span
                            class="text-xs text-muted-foreground"
                            >{m.coverage_years_uncounted({
                              years: missing,
                            })}</span
                          >{/if}</span
                      ></td
                    >
                  {/if}
                  {#each used as column, index (column)}
                    <td
                      class={cn("px-3 py-2.5", {
                        "border-s": isUs && opens(used, index),
                      })}
                    >
                      {@render cell(
                        level.cells[slot(column, row.key)],
                        isUs && row.key === "local",
                      )}
                    </td>
                  {/each}
                </tr>
                {#if gaps.length > 0 || unlisted.length > 0 || general.length > 0}
                  <tr>
                    <td
                      colspan={used.length + (isUs ? 1 : 2)}
                      class="px-3.5 pbe-2.5"
                    >
                      <span
                        class="grid gap-1.5 text-sm text-muted-foreground max-inline-[80ch]"
                      >
                        {#each gaps as note, index (index)}
                          <span>
                            <span
                              class="font-semibold text-foreground tabular-nums"
                              >{m.coverage_ledger_uncounted()}
                              {note.from === note.to
                                ? note.from
                                : `${note.from}–${note.to}`}.</span
                            >
                            {note.note}
                            {#if (note.held ?? 0) > 0}
                              {m.coverage_ledger_held({
                                count: count(note.held ?? 0),
                              })}
                            {/if}
                          </span>
                        {/each}
                        {#each general as note, index (index)}
                          <span>{note}</span>
                        {/each}
                        {#if unlisted.length > 0}
                          <span
                            >{m.coverage_ledger_unlisted({
                              years: unlisted
                                .map(
                                  (note) =>
                                    `${note.from} (${count(note.held ?? 0)})`,
                                )
                                .join(", "),
                            })}</span
                          >
                        {/if}
                      </span>
                    </td>
                  </tr>
                {/if}
              {/each}
            </tbody>
          </table>
          <details class="group md:hidden">
            <summary
              class="flex cursor-pointer list-none items-center gap-1 text-sm font-bold focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden"
            >
              <ChevronRightIcon
                class="text-muted-foreground transition-transform block-4 inline-4 group-open:rotate-90"
              />
              {m.coverage_ledger()}
            </summary>
            <ul class="grid">
              {#each row.levels as level (level.key)}
                {const notes = $derived(notesFor(level.key))}
                {const gaps = $derived(
                  notes.filter(
                    (note) => note.from != null && note.listed !== false,
                  ),
                )}
                {const counted = $derived(
                  spans(
                    level.years,
                    gaps.flatMap((note) =>
                      note.from == null ? [] : [note.from],
                    ),
                  ),
                )}
                <li class="grid gap-2 border-bs py-2.5">
                  <span class="font-semibold"
                    >{levelLabel(data.polity, level.key)}</span
                  >
                  {#if !isUs}
                    {@render strip(
                      levelLabel(data.polity, level.key),
                      level.years,
                      gaps,
                      counted,
                    )}
                    <span class="text-sm tabular-nums"
                      ><span class="text-muted-foreground"
                        >{m.coverage_column_years()}:</span
                      >
                      {counted === ""
                        ? m.coverage_ledger_nothing()
                        : counted}</span
                    >
                    {#if gaps.length > 0}
                      <span class="text-xs text-muted-foreground tabular-nums"
                        >{m.coverage_years_uncounted({
                          years: gaps
                            .map((note) =>
                              note.from === note.to
                                ? String(note.from)
                                : `${note.from}–${note.to}`,
                            )
                            .join(", "),
                        })}</span
                      >
                    {/if}
                  {/if}
                  {@render cards(level.cells, row.key)}
                  {const remark = $derived(
                    summary.levels.find((found) => found.key === level.key)
                      ?.note,
                  )}
                  {#if remark != null}
                    <p class="text-sm text-muted-foreground">{remark}</p>
                  {/if}
                  {#each notes as note, index (index)}
                    <p class="text-sm text-muted-foreground">
                      {#if note.from != null && note.listed !== false}
                        <span class="font-semibold text-foreground tabular-nums"
                          >{m.coverage_ledger_uncounted()}
                          {note.from === note.to
                            ? note.from
                            : `${note.from}–${note.to}`}.</span
                        >
                      {/if}
                      {note.listed === false
                        ? m.coverage_ledger_unlisted({
                            years: `${note.from} (${count(note.held ?? 0)})`,
                          })
                        : note.note}
                      {#if note.listed !== false && (note.held ?? 0) > 0}
                        {m.coverage_ledger_held({
                          count: count(note.held ?? 0),
                        })}
                      {/if}
                    </p>
                  {/each}
                </li>
              {/each}
            </ul>
          </details>
        </div>
      </section>
    {/each}

    {#if data.tiers.length > 0}
      <section aria-labelledby="coverage-where" class="grid gap-px bg-border">
        <div
          class="grid grid-cols-[minmax(0,1fr)] gap-2 bg-card px-3.5 pbs-4 pbe-3"
        >
          <h2 id="coverage-where" class="text-lg font-bold">
            {m.coverage_where()}
          </h2>
          <p class="text-sm text-muted-foreground">
            {(isUs
              ? m.coverage_where_hint
              : data.isFlat
                ? m.coverage_where_hint_flat
                : m.coverage_where_hint_country)()}
          </p>
          <div
            role="group"
            aria-label={m.coverage_where_tiers()}
            class="flex overflow-x-auto rounded-sm border border-border p-0.5 text-sm inline-fit max-inline-full min-inline-0"
          >
            {#each data.tiers as option (option.key)}
              <button
                type="button"
                aria-pressed={tab === option.key}
                class={cn(
                  "shrink-0 rounded-[3px] px-2 whitespace-nowrap block-7 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
                  {
                    "bg-foreground text-background": tab === option.key,
                    "text-muted-foreground hover:text-foreground":
                      tab !== option.key,
                  },
                )}
                onclick={() => (picked = option.key)}
                >{TIER_LABELS[option.key as Tier]()}</button
              >
            {/each}
          </div>
          <div class="flex flex-wrap items-center gap-2">
            {#if options.length > 1}
              <div
                role="group"
                aria-label={m.coverage_goal_tabs()}
                class="flex overflow-x-auto rounded-sm border border-border p-0.5 text-sm max-inline-full min-inline-0"
              >
                {#each options as option (option)}
                  <button
                    type="button"
                    aria-pressed={goal === option}
                    class={cn(
                      "shrink-0 rounded-[3px] px-2 whitespace-nowrap block-7 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
                      {
                        "bg-foreground text-background": goal === option,
                        "text-muted-foreground hover:text-foreground":
                          goal !== option,
                      },
                    )}
                    onclick={() => (grain = option)}
                    >{(option === "shapes"
                      ? isUs
                        ? m.coverage_gap_shapes
                        : m.coverage_gap_country_shapes
                      : option === tab && !isUs
                        ? m.coverage_gap_constituency
                        : (GAP_LABELS[option] ??
                          m.coverage_gap_local))()}</button
                  >
                {/each}
              </div>
            {/if}
            <div
              role="group"
              aria-label={m.coverage_sort()}
              class="flex rounded-sm border border-border p-0.5 text-sm inline-fit"
            >
              {#each ["gap", "name"] as const as option (option)}
                <button
                  type="button"
                  aria-pressed={order === option}
                  class={cn(
                    "rounded-[3px] px-2 block-7 focus-visible:outline-2 focus-visible:outline-ring",
                    {
                      "bg-foreground text-background": order === option,
                      "text-muted-foreground hover:text-foreground":
                        order !== option,
                    },
                  )}
                  onclick={() => (order = option)}
                  >{option === "gap"
                    ? m.coverage_sort_gap()
                    : m.coverage_sort_name()}</button
                >
              {/each}
            </div>
          </div>
          <p class="text-xs text-muted-foreground max-inline-[80ch]">
            {m.coverage_column_note()}
          </p>
        </div>
        <div
          role="treegrid"
          aria-label={m.coverage_where()}
          class="grid gap-px"
        >
          <div
            role="row"
            class="grid grid-cols-[minmax(0,1fr)_3.5rem_7rem] items-end gap-x-3 bg-card px-3.5 py-1.5 text-xs/tight font-bold sm:grid-cols-[minmax(0,1fr)_5rem_16rem] lg:grid-cols-[minmax(0,30rem)_5rem_16rem]"
          >
            <span role="columnheader"
              >{(isUs
                ? m.coverage_column_name
                : data.isFlat
                  ? m.coverage_column_name_flat
                  : m.coverage_column_name_country)()}</span
            >
            <span role="columnheader" class="text-end"
              >{m.coverage_column_short()}</span
            >
            <span role="columnheader">{m.coverage_column_done()}</span>
          </div>
          {#key `${tab}:${goal}`}
            <Gaps
              polity={data.polity}
              {goal}
              tier={tab}
              {order}
              place={data.isFlat ? "" : null}
              start={data.isFlat ? 1 : 0}
              rows={goal === headline(data.polity, tab).goal
                ? (data.gaps[tab] ?? null)
                : null}
            />
          {/key}
        </div>
      </section>
    {/if}

    {#if Object.keys(summary.sources).length > 0}
      <section
        aria-labelledby="coverage-sources"
        class="grid gap-2 bg-card px-3.5 pbs-4 pbe-4"
      >
        <h2 id="coverage-sources" class="text-lg font-bold">
          {m.coverage_sources()}
        </h2>
        <p class="text-sm text-muted-foreground">{m.coverage_sources_hint()}</p>
        <ul class="grid gap-2 text-sm">
          {#each Object.entries(summary.sources) as [key, source] (key)}
            <li class="grid gap-0.5 max-inline-[80ch]">
              <a
                href={source.url}
                rel="external noopener"
                class="font-semibold text-pretty underline decoration-border underline-offset-2 hover:decoration-foreground focus-visible:outline-2 focus-visible:outline-ring"
                >{source.title}</a
              >
              {#if source.publisher != null}
                <span class="text-muted-foreground">{source.publisher}</span>
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  </article>
</main>
