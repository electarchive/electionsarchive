<script lang="ts">
  import { goto, invalidateAll } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { Switch } from "#lib/components/ui/switch/index.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import type { CountryGoal, Counts, Reason } from "#lib/coverage.js";
  import Bar from "#routes/coverage/bar.svelte";
  import Gaps from "#routes/coverage/gaps.svelte";
  import {
    COUNTRY_GOALS,
    isCountryGoal,
    label,
    levelLabel,
    REASONS,
    spans,
  } from "#lib/coverage.js";
  import Tone from "#routes/coverage/tone.svelte";

  const { data }: { data: PageData } = $props();

  const TITLES: Record<CountryGoal, () => string> = {
    national: m.coverage_country_goal_national,
    fine: m.coverage_country_goal_fine,
    shapes: m.coverage_country_goal_shapes,
    subnational: m.coverage_country_goal_subnational,
    local: m.coverage_country_goal_local,
    referendums: m.coverage_country_goal_referendums,
    ep: m.coverage_country_goal_ep,
  };
  const TABS: Record<CountryGoal, () => string> = {
    national: m.coverage_country_tab_national,
    fine: m.coverage_country_tab_fine,
    shapes: m.coverage_country_tab_shapes,
    subnational: m.coverage_country_tab_subnational,
    local: m.coverage_country_tab_local,
    referendums: m.coverage_country_tab_referendums,
    ep: m.coverage_country_tab_ep,
  };
  const COMPLETE: Record<CountryGoal, () => string> = {
    national: m.coverage_country_def_national,
    fine: m.coverage_country_def_fine,
    shapes: m.coverage_country_def_shapes,
    subnational: m.coverage_country_def_subnational,
    local: m.coverage_country_def_local,
    referendums: m.coverage_country_def_referendums,
    ep: m.coverage_country_def_ep,
  };
  const DEFINITIONS: Partial<Record<Reason, () => string>> = {
    coarse: m.coverage_country_reason_coarse_def,
    totals: m.coverage_country_reason_totals_def,
    bare: m.coverage_country_reason_bare_def,
    absent: m.coverage_country_reason_absent_def,
    unshaped: m.coverage_country_reason_unshaped_def,
    undrawn: m.coverage_country_reason_undrawn_def,
  };

  const year = new Date().getFullYear();
  const summary = $derived(data.summary);
  const rows = $derived(
    data.goals.flatMap((row) =>
      isCountryGoal(row.goal) ? [{ ...row, goal: row.goal }] : [],
    ),
  );
  const goals = $derived(
    COUNTRY_GOALS.flatMap((goal) => {
      const levels = summary.levels.filter((level) =>
        goal === "fine"
          ? level.fine
          : goal === "shapes"
            ? level.goal === "national" && level.unit === "district"
            : level.goal === goal,
      );
      const row = rows.find((found) => found.goal === goal) ?? null;
      return row == null && levels.length === 0 ? [] : [{ goal, row, levels }];
    }),
  );
  const lead = $derived(goals.find((goal) => goal.goal === "national"));
  const next = $derived(goals.filter((goal) => goal.goal !== "national"));
  const tabs = $derived(
    COUNTRY_GOALS.filter((goal) => rows.some((row) => row.goal === goal)),
  );
  let picked = $state<CountryGoal | null>(null);
  const goal = $derived(picked ?? tabs[0] ?? "national");
  let order = $state<"gap" | "name">("gap");

  const range = $derived.by(() => {
    const years = [
      summary.first ?? year,
      summary.last ?? year,
      ...data.counted.flatMap((level) => level.years),
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
      if (share > 0.06 && share < 0.94) {
        found.push(at);
      }
    }
    return found;
  });
  const at = (value: number) =>
    ((value - range.from) / (range.to - range.from + 1)) * 100;

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
  const path = $derived(resolve("/coverage/[cc]", { cc: data.polity }));
</script>

<svelte:head>
  <title
    >{m.coverage_country_title({ name: summary.name })} | {m.wordmark()}</title
  >
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <meta name="robots" content="noindex" />
</svelte:head>

{#snippet progress(row: { goal: CountryGoal } & Counts, isLead: boolean)}
  <div class="grid gap-3">
    <p class="flex flex-wrap items-baseline gap-x-3 gap-y-1 tabular-nums">
      <span
        class={cn("font-bold tracking-[-0.01em]", {
          "text-3xl": isLead,
          "text-xl": !isLead,
        })}
      >
        {m.coverage_progress({
          complete: count(row.complete),
          total: count(row.total),
        })}
      </span>
      <span class="text-muted-foreground">
        {m.coverage_percent({
          percent: percent(row.complete / Math.max(1, row.total), 1),
        })}
      </span>
      <span class="text-muted-foreground">
        {m.coverage_short({ count: count(row.total - row.complete) })}
      </span>
    </p>
    <Bar
      counts={row}
      goal={row.goal}
      polity={data.polity}
      class={isLead ? "block-4" : "block-3"}
    />
    <ul class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {#each REASONS[row.goal].filter((reason) => reason === "complete" || row[reason] > 0) as reason (reason)}
        <li class="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-x-2">
          <Tone {reason} class="mbs-1 rounded-xs block-3 inline-3" />
          <span>
            <span class="font-semibold"
              >{label(row.goal, reason, data.polity)}</span
            >
            <span class="tabular-nums">{count(row[reason])}</span>
            <span class="block text-muted-foreground"
              >{reason === "complete"
                ? COMPLETE[row.goal]()
                : (DEFINITIONS[reason]?.() ?? "")}</span
            >
          </span>
        </li>
      {/each}
    </ul>
  </div>
{/snippet}

{#snippet ledger(
  goal: CountryGoal,
  levels: Array<{ key: string; goal: string }>,
)}
  <div class="grid gap-2">
    <div
      class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground"
    >
      <h3 class="text-sm font-bold text-foreground">{m.coverage_ledger()}</h3>
      <span class="flex items-center gap-1.5">
        <span aria-hidden="true" class="bg-foreground block-3 inline-0.5"
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
    </div>
    <ul class="grid">
      <li
        aria-hidden="true"
        class="hidden border-be pbe-1 text-xs text-muted-foreground tabular-nums sm:grid sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)] sm:gap-x-4"
      >
        <span></span>
        <span class="relative block-4">
          <span class="absolute inset-s-0">{range.from}</span>
          {#each marks as mark (mark)}
            <span
              class="absolute -translate-x-1/2"
              style:inset-inline-start="{at(mark)}%">{mark}</span
            >
          {/each}
          <span class="absolute inset-e-0">{range.to}</span>
        </span>
      </li>
      {#each levels as level (level.key)}
        {const years = $derived(
          data.counted.find((found) => found.level === level.key)?.years ?? [],
        )}
        {const notes = $derived(
          summary.notes.filter(
            (note) =>
              note.level === level.key &&
              (note.goal === level.goal || note.goal === goal),
          ),
        )}
        {const gaps = $derived(
          notes.filter((note) => note.from != null && note.listed !== false),
        )}
        {const unlisted = $derived(
          notes.filter((note) => note.listed === false && note.from != null),
        )}
        {const general = $derived(notes.filter((note) => note.from == null))}
        {const name = $derived(levelLabel(data.polity, level.key))}
        {const counted = $derived(
          spans(
            years,
            gaps.flatMap((note) => (note.from == null ? [] : [note.from])),
          ),
        )}
        {const uncounted = $derived(
          gaps
            .map((note) =>
              note.from === note.to
                ? String(note.from)
                : `${note.from}–${note.to}`,
            )
            .join(", "),
        )}
        <li
          class="grid gap-x-4 gap-y-1.5 border-be py-2.5 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]"
        >
          <span class="text-sm font-semibold text-pretty">{name}</span>
          <div class="grid gap-1.5 min-inline-0">
            <span
              role="img"
              aria-label={uncounted === ""
                ? m.coverage_ledger_strip_counted({ label: name, counted })
                : m.coverage_ledger_strip({
                    label: name,
                    counted:
                      counted === "" ? m.coverage_ledger_nothing() : counted,
                    uncounted,
                  })}
              class="relative block overflow-hidden border-be border-border block-4"
            >
              {#each gaps as note, index (index)}
                <span
                  class="absolute inset-be-0 border border-be-0 border-muted-foreground/60 bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_4px)] block-2.5"
                  style:inset-inline-start="{at(note.from ?? range.from)}%"
                  style:inline-size="{at((note.to ?? range.to) + 1) -
                    at(note.from ?? range.from)}%"
                ></span>
              {/each}
              {#each years as counted (counted)}
                <span
                  class="absolute inset-be-0 bg-foreground block-full inline-0.5"
                  style:inset-inline-start="{at(counted)}%"
                ></span>
              {/each}
            </span>
            <p class="text-sm tabular-nums">
              {#if counted === ""}
                <span class="font-semibold">{m.coverage_ledger_nothing()}</span>
              {:else}
                <span class="font-semibold">{m.coverage_ledger_counted()}</span>
                {counted}
              {/if}
            </p>
            {#each gaps as note, index (index)}
              <p class="text-sm text-muted-foreground max-inline-[72ch]">
                <span class="font-semibold text-foreground tabular-nums"
                  >{m.coverage_ledger_uncounted()}
                  {note.from === note.to
                    ? note.from
                    : `${note.from}–${note.to}`}</span
                >
                {note.note}
                {#if (note.held ?? 0) > 0}
                  <span class="tabular-nums"
                    >{m.coverage_ledger_held({
                      count: count(note.held ?? 0),
                    })}</span
                  >
                {/if}
              </p>
            {/each}
            {#each general as note, index (index)}
              <p class="text-sm text-muted-foreground max-inline-[72ch]">
                {note.note}
              </p>
            {/each}
            {#if unlisted.length > 0}
              <p class="text-sm text-muted-foreground max-inline-[72ch]">
                {m.coverage_ledger_unlisted({
                  years: unlisted
                    .map((note) => `${note.from} (${count(note.held ?? 0)})`)
                    .join(", "),
                })}
              </p>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
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
      <h1 class="title">{m.coverage_country_title({ name: summary.name })}</h1>
      <p class="text-[0.9375rem] text-muted-foreground max-inline-[72ch]">
        {m.coverage_country_lede()}
      </p>
      {#if summary.first != null && summary.last != null}
        <p class="text-[0.9375rem] tabular-nums max-inline-[72ch]">
          {m.coverage_country_facts({
            elections: count(summary.elections),
            contests: count(summary.contests),
            first: String(summary.first),
            last: String(summary.last),
          })}
          {#if summary.finestNoun != null}
            {m.coverage_country_finest({ noun: summary.finestNoun })}
          {/if}
        </p>
      {/if}
      <div class="grid gap-1 pbs-1">
        <label class="flex items-center gap-2 text-[0.8125rem] font-semibold">
          <Switch
            checked={!data.current}
            onCheckedChange={(value: boolean) =>
              void goto(value ? path : `${path}?current=1`, {
                replace: true,
                reset: false,
              })}
          />
          {m.coverage_current({ year: String(year) })}
        </label>
        <p class="text-xs text-muted-foreground">
          {m.coverage_current_hint({ year: String(year) })}
        </p>
      </div>
      <p class="text-xs text-muted-foreground">{built}</p>
    </header>
    {#if lead != null}
      <section
        aria-labelledby="coverage-lead"
        class="grid gap-4 bg-card px-3.5 py-4"
      >
        <hgroup class="grid gap-1">
          <p
            class="text-xs font-bold tracking-wide text-muted-foreground uppercase"
          >
            {m.coverage_goal_now()}
          </p>
          <h2
            id="coverage-lead"
            class="text-lg/snug font-bold max-inline-[60ch]"
          >
            {TITLES.national()}
          </h2>
        </hgroup>
        {#if lead.row != null}
          {@render progress(lead.row, true)}
        {:else}
          <p class="text-sm text-muted-foreground">
            {m.coverage_country_none()}
          </p>
        {/if}
        {@render ledger("national", lead.levels)}
      </section>
    {/if}
    {#if next.length > 0}
      <section aria-labelledby="coverage-next" class="grid bg-card">
        <hgroup class="grid gap-0.5 px-3.5 pbs-4 pbe-2">
          <h2 id="coverage-next" class="text-lg font-bold">
            {m.coverage_next()}
          </h2>
          <p class="text-sm text-muted-foreground">{m.coverage_next_hint()}</p>
        </hgroup>
        {#each next as item (item.goal)}
          <details class="group border-bs">
            <summary
              class="flex cursor-pointer list-none items-start gap-2 px-3.5 py-2.5 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden"
            >
              <ChevronRightIcon
                class="mbs-0.5 shrink-0 text-muted-foreground transition-transform block-4 inline-4 group-open:rotate-90"
              />
              <span class="grid flex-1 gap-1 min-inline-0">
                <span class="font-semibold text-pretty max-inline-[70ch]"
                  >{TITLES[item.goal]()}</span
                >
                {#if item.row != null}
                  <span
                    class="text-sm text-muted-foreground tabular-nums group-open:hidden"
                  >
                    {m.coverage_progress({
                      complete: count(item.row.complete),
                      total: count(item.row.total),
                    })},
                    {m.coverage_percent({
                      percent: percent(
                        item.row.complete / Math.max(1, item.row.total),
                        1,
                      ),
                    })}
                  </span>
                  <Bar
                    counts={item.row}
                    goal={item.goal}
                    polity={data.polity}
                    class="block-1.5 max-inline-80 group-open:hidden"
                  />
                {:else}
                  <span class="text-sm text-muted-foreground group-open:hidden"
                    >{m.coverage_overview_none()}</span
                  >
                {/if}
              </span>
            </summary>
            <div class="grid gap-4 px-3.5 ps-9.5 pbe-4">
              {#if item.row != null}
                {@render progress(item.row, false)}
              {:else}
                <p class="text-sm text-muted-foreground">
                  {m.coverage_country_none()}
                </p>
              {/if}
              {@render ledger(item.goal, item.levels)}
            </div>
          </details>
        {/each}
      </section>
    {/if}
    {#if tabs.length > 0}
      <section aria-labelledby="coverage-where" class="grid gap-px bg-border">
        <div
          class="grid grid-cols-[minmax(0,1fr)] gap-2 bg-card px-3.5 pbs-4 pbe-3"
        >
          <h2 id="coverage-where" class="text-lg font-bold">
            {m.coverage_where()}
          </h2>
          <p class="text-sm text-muted-foreground">
            {(data.isFlat
              ? m.coverage_where_hint_flat
              : m.coverage_where_hint_country)()}
          </p>
          <div class="flex flex-wrap items-center gap-2">
            <div
              role="group"
              aria-label={m.coverage_goal_tabs()}
              class="flex overflow-x-auto rounded-sm border border-border p-0.5 text-sm max-inline-full min-inline-0"
            >
              {#each tabs as option (option)}
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
                  onclick={() => (picked = option)}>{TABS[option]()}</button
                >
              {/each}
            </div>
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
          <p class="text-sm text-muted-foreground">{TITLES[goal]()}</p>
          <p class="text-xs text-muted-foreground">
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
              >{(data.isFlat
                ? m.coverage_column_name_flat
                : m.coverage_column_name_country)()}</span
            >
            <span role="columnheader" class="text-end"
              >{m.coverage_column_short()}</span
            >
            <span role="columnheader">{m.coverage_column_done()}</span>
          </div>
          {#key `${goal}:${data.current}`}
            <Gaps
              polity={data.polity}
              {goal}
              {order}
              current={data.current}
              place={data.isFlat ? "" : null}
              start={data.isFlat ? 1 : 0}
              rows={goal === "national" ? data.gaps : null}
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
