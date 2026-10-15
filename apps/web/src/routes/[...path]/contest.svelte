<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import { count, date, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Electors from "./electors.svelte";
  import PlaceSwitcher from "./place-switcher.svelte";
  import Places from "./places.svelte";
  import Seats from "./seats.svelte";

  const {
    contest,
    match,
  }: {
    contest: Extract<PageData, { kind: "contest" }>["contest"];
    match: string;
  } = $props();

  const OUTCOMES: Record<string, () => string> = {
    elected: m.outcome_elected,
    won: m.outcome_won,
    advanced: m.outcome_advanced,
    tied: m.outcome_tied,
  };
  const STAGES: Record<string, () => string> = {
    live: m.stage_live,
    preliminary: m.stage_preliminary,
    official: m.stage_official,
    certified: m.stage_certified,
    court_revised: m.stage_court_revised,
    recount: m.stage_recount,
    alternative: m.stage_alternative,
  };
  const DECLARED: Record<string, () => string> = {
    tacit: m.declared_tacit,
    show_of_hands: m.declared_show_of_hands,
    appointed: m.declared_appointed,
  };
  const STATUSES: Record<string, () => string> = {
    superseded: m.status_superseded,
    annulled: m.status_annulled,
    withdrawn: m.status_withdrawn,
  };

  let expanded = $state(false);

  const here = $derived(page.url.pathname.slice(1));
  const drilled = $derived(contest.scope.noun != null);
  const electoral = $derived(
    contest.kind === "office" || contest.kind === "elector_slate",
  );
  const question = $derived(
    contest.rows.some((row) => row.key.startsWith("answer:")),
  );
  const year = $derived(contest.target?.date?.slice(0, 4) ?? "");
  const seated = $derived(
    contest.rows.some((row) => row.current?.seats != null),
  );
  const topShare = $derived(
    Math.max(0.0001, ...contest.rows.map((row) => row.current?.share ?? 0)),
  );
  const topDelta = $derived(
    Math.max(0.0001, ...contest.rows.map((row) => Math.abs(row.delta ?? 0))),
  );
  const folded = $derived.by(() => {
    const tail = contest.rows.filter(
      (row, index) =>
        index >= 5 &&
        (row.current?.share ?? 0) < 0.01 &&
        (row.earlier?.share ?? 0) < 0.01 &&
        row.current?.outcome == null &&
        row.current?.seats == null,
    );
    return new Set(tail.length > 2 ? tail.map((row) => row.key) : []);
  });
  const shown = $derived(
    expanded
      ? contest.rows
      : contest.rows.filter((row) => !folded.has(row.key)),
  );
  const tail = $derived.by(() => {
    let votes = 0;
    let share = 0;
    for (const row of contest.rows) {
      if (folded.has(row.key)) {
        votes += row.current?.votes ?? 0;
        share += row.current?.share ?? 0;
      }
    }
    return { votes, share };
  });
  const parties = $derived(
    contest.rows
      .filter((row) => (row.current?.seats ?? 0) > 0)
      .map((row) => ({
        key: row.key,
        label: row.label,
        short: row.short,
        color: row.color,
        seats: row.current?.seats ?? 0,
      }))
      .toSorted((a, b) => b.seats - a.seats),
  );
  const chamber = $derived.by(() => {
    let total = 0;
    for (const party of parties) {
      total += party.seats;
    }
    return !electoral && parties.length > 1 && total >= 15;
  });
  const years = $derived.by(() => {
    const seen = Object.groupBy(
      contest.series,
      (entry) => entry.date?.slice(0, 4) ?? "",
    );
    return contest.series.map((entry) => {
      const key = entry.date?.slice(0, 4) ?? "";
      const label =
        (seen[key]?.length ?? 0) > 1 && entry.date != null
          ? new Intl.DateTimeFormat(getLocale(), {
              month: "short",
              year: "numeric",
              timeZone: "UTC",
            }).format(new Date(`${entry.date}T00:00:00Z`))
          : key;
      return { ...entry, label };
    });
  });
  const carried = $derived.by(() => {
    const query = new URLSearchParams(
      [...page.url.searchParams].filter(([key]) => key !== "vs"),
    )
      .toString()
      .replaceAll("%2F", "/");
    return query === "" ? ("" as const) : (`?${query}` as const);
  });
  const items = $derived([
    { value: "none", label: m.compare_none() },
    ...contest.targets.map((target) => ({
      value: target.value,
      label: target.annulled
        ? m.compare_annulled({ label: target.label })
        : target.label,
      detail: date(target.detail),
      group: target.series ? m.compare_series() : m.compare_other(),
      keywords: [target.detail ?? ""],
    })),
  ]);
  const matches = $derived(
    [
      { value: "party", label: m.match_party() },
      { value: "person", label: m.match_person() },
      { value: "family", label: m.match_family() },
    ].map((option) => ({
      ...option,
      query: new URLSearchParams({
        ...Object.fromEntries(page.url.searchParams),
        match: option.value,
      })
        .toString()
        .replaceAll("%2F", "/"),
    })),
  );
  const publishers = $derived([
    ...new Set(contest.current?.sets.map((set) => set.source.publisher) ?? []),
  ]);
  const captions = $derived([
    ...new Set(
      contest.current?.sets.map((set) =>
        m.sources_caption({
          stage: STAGES[set.stage]?.() ?? set.stage,
          publisher: set.source.publisher,
        }),
      ) ?? [],
    ),
  ]);
  const notes = $derived([
    ...contest.comparison.notes.map((note) => {
      if (note.code === "partial") {
        const value = percent(Number(note.value));
        return note.side === "target"
          ? m.note_partial_target({ percent: value })
          : m.note_partial_current({ percent: value });
      }
      if (note.code === "alternative") {
        const publisher = note.value ?? "";
        return note.side === "target"
          ? m.note_alternative_target({ publisher })
          : m.note_alternative_current({ publisher });
      }
      return {
        no_target: m.note_no_target,
        not_covered: m.note_not_covered,
        different_race: m.note_different_race,
        annulled: m.note_annulled,
      }[note.code]();
    }),
    ...contest.notes.map((note) => note.body),
  ]);
  const checks = $derived(
    contest.checks.map((check) => ({
      code: check.code,
      summary:
        check.code === "accounting.turnout"
          ? m.checks_turnout({ count: check.units.length })
          : m.checks_votes_valid({ count: check.units.length }),
      units: check.units.map((unit) =>
        check.code === "accounting.turnout"
          ? m.check_turnout_unit({
              name: unit.name,
              ballots: count(unit.details.ballots_cast ?? 0),
              registered: count(unit.details.registered ?? 0),
            })
          : m.check_votes_valid_unit({
              name: unit.name,
              votes: count(unit.details.votes ?? 0),
              valid: count(unit.details.valid ?? 0),
            }),
      ),
    })),
  );
</script>

<header
  class="col-span-12 grid gap-x-8 gap-y-3 bg-card px-3.5 pbs-4 pbe-3.5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end"
>
  <div class="grid gap-1.5 min-inline-0">
    {#if drilled && contest.path != null}
      <a
        class="justify-self-start text-[0.9375rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
        href={resolve("/[...path]", { path: contest.path })}>{contest.name}</a
      >
    {/if}
    <h1
      class="font-serif text-[clamp(1.75rem,3vw,2.5rem)] leading-[1.02] font-[640] tracking-[-0.01em] text-balance [view-transition-name:scope-title]"
      data-scope-title
    >
      {drilled ? contest.scope.name : contest.name}
    </h1>
    <p
      class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem] text-muted-foreground"
    >
      {#if drilled}
        <span class="first-letter:uppercase"
          >{m.scope_in({
            noun: contest.scope.noun ?? "",
            parent: contest.scope.parent ?? "",
          })}</span
        >
      {:else if contest.election.path == null}
        <span>{contest.election.name}</span>
      {:else}
        <a
          class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={resolve("/[...path]", { path: contest.election.path })}
          >{contest.election.name}</a
        >
      {/if}
      <time datetime={contest.election.date}>{date(contest.election.date)}</time
      >
      {#if contest.election.status === "annulled"}
        <span class="highlighter px-1 font-semibold text-foreground"
          >{m.election_annulled()}</span
        >
      {/if}
    </p>
  </div>
  {#if years.length > 1}
    <nav
      class="max-md:-mx-3.5 max-md:overflow-x-auto max-md:px-3.5"
      aria-label={m.series_label()}
    >
      <ol class="flex inline-max">
        {#each years as entry (entry.path ?? entry.label)}
          <li class="-ms-px first:ms-0">
            {#if entry.current || entry.path == null}
              <span
                class={cn(
                  "flex items-center border border-input bg-card px-2.5 font-mono text-[0.8125rem] tabular-nums block-8",
                  {
                    "relative z-10 border-foreground highlighter font-semibold":
                      entry.current,
                    "line-through": entry.annulled,
                  },
                )}
                aria-current={entry.current ? "page" : undefined}
                title={entry.annulled
                  ? m.series_annulled({ year: entry.label })
                  : undefined}>{entry.label}</span
              >
            {:else}
              <a
                class={cn(
                  "flex items-center border border-input bg-card px-2.5 font-mono text-[0.8125rem] text-muted-foreground tabular-nums block-8 hover:relative hover:z-10 hover:border-foreground hover:text-foreground",
                  { "line-through": entry.annulled },
                )}
                href={resolve(`/[...path]${carried}`, { path: entry.path })}
                title={entry.annulled
                  ? m.series_annulled({ year: entry.label })
                  : undefined}>{entry.label}</a
              >
            {/if}
          </li>
        {/each}
      </ol>
    </nav>
  {/if}
  {#if contest.scope.trail.length > 1}
    <nav class="md:col-span-2" aria-label={m.scope_path()}>
      <ol class="flex flex-wrap items-center gap-y-1 text-sm">
        {#each contest.scope.trail as crumb, index (index)}
          <li class="flex items-center">
            {#if index > 0}
              <span class="px-1.5 text-muted-foreground" aria-hidden="true"
                >/</span
              >
            {/if}
            {#if index < contest.scope.trail.length - 1 && crumb.path != null}
              <a
                class="hover:highlighter"
                href={resolve("/[...path]", { path: crumb.path })}
                >{crumb.name}</a
              >
            {:else}
              <span class="font-semibold" aria-current="page">{crumb.name}</span
              >
            {/if}
            {#if crumb.siblings != null}
              <PlaceSwitcher
                contest={crumb.siblings.contest}
                unit={crumb.siblings.unit}
                level={crumb.siblings.level}
                noun={crumb.siblings.noun}
                current={crumb.name}
              />
            {/if}
          </li>
        {/each}
      </ol>
    </nav>
  {/if}
</header>

{#if contest.current == null && contest.declared != null}
  <section class="col-span-12 grid gap-2.5 bg-card px-3.5 py-4">
    <p class="text-[0.9375rem] text-muted-foreground">
      {DECLARED[contest.declared.kind]?.() ?? ""}
    </p>
    <ul class="grid gap-1.5 text-sm/tight">
      {#each contest.declared.rows as row (row.key)}
        <li
          class="flex items-baseline gap-2"
          style:--party={swatch(row.color, row.key)}
        >
          <span
            class="shrink-0 self-center rounded-xs bg-(--party) block-2.5 inline-2.5"
          ></span>
          <span class="min-inline-0">
            <span class="font-semibold">{row.label}</span>
            {#if row.short != null && row.short !== row.label}
              <span class="ms-1 text-[0.8125rem] text-muted-foreground"
                >{row.short}</span
              >
            {/if}
            <span
              class="ms-1 highlighter px-1 text-xs font-semibold text-foreground"
              >{OUTCOMES[row.outcome]?.() ?? row.outcome}</span
            >
          </span>
        </li>
      {/each}
    </ul>
  </section>
{:else if contest.current == null}
  <p class="col-span-12 bg-card px-3.5 py-6 text-muted-foreground">
    {m.no_results()}
  </p>
{:else}
  {#if contest.kind === "office" && !drilled && parties.length > 0}
    <Electors {parties} />
  {/if}
  <dl class="col-span-12 grid grid-cols-12 gap-px">
    <div class="col-span-6 bg-card px-3.5 py-3 md:col-span-3">
      <dt class="text-[0.8125rem] text-muted-foreground">{m.stat_valid()}</dt>
      <dd class="mbs-0.5 font-mono text-2xl tracking-[-0.02em] tabular-nums">
        {count(contest.current.valid)}
      </dd>
    </div>
    <div class="col-span-6 bg-card px-3.5 py-3 md:col-span-3">
      <dt class="text-[0.8125rem] text-muted-foreground">
        {m.stat_options()}
      </dt>
      <dd class="mbs-0.5 font-mono text-2xl tracking-[-0.02em] tabular-nums">
        {contest.rows.filter((row) => row.current != null).length}
      </dd>
    </div>
    {#if contest.target != null}
      <div class="col-span-6 bg-card px-3.5 py-3 md:col-span-3">
        <dt class="text-[0.8125rem] text-muted-foreground">
          {m.stat_earlier_valid({ year })}
        </dt>
        <dd class="mbs-0.5 font-mono text-2xl tracking-[-0.02em] tabular-nums">
          {count(contest.target.valid)}
        </dd>
      </div>
      <div class="col-span-6 bg-card px-3.5 py-3 md:col-span-3">
        <dt class="text-[0.8125rem] text-muted-foreground">
          {m.stat_comparison()}
        </dt>
        <dd class="mbs-1">
          <span class="highlighter px-1 text-lg font-bold">
            {contest.comparison.label === "exact"
              ? m.label_exact()
              : m.label_estimated()}
          </span>
          <span class="mbs-1 block text-[0.8125rem] text-muted-foreground">
            {contest.comparison.label === "exact"
              ? m.label_exact_detail()
              : m.label_estimated_detail()}
          </span>
        </dd>
      </div>
    {:else}
      <div class="col-span-6 bg-card max-md:hidden"></div>
    {/if}
  </dl>

  <div
    class="col-span-12 flex flex-wrap items-center gap-x-6 gap-y-3 bg-card px-3.5 py-3"
  >
    <div class="flex flex-1 basis-80 items-center gap-2.5 min-inline-0">
      <span class="shrink-0 text-sm font-semibold">{m.compare_label()}</span>
      <Combobox
        class="max-inline-md"
        {items}
        value={contest.target?.path ??
          (page.url.searchParams.get("vs") === "none" ? "none" : null)}
        label={m.compare_label()}
        placeholder={m.compare_placeholder()}
        search={m.compare_search()}
        empty={m.compare_empty()}
        onValueChange={(value) => {
          const query = new URLSearchParams({
            ...Object.fromEntries(page.url.searchParams),
            vs: value ?? "none",
          })
            .toString()
            .replaceAll("%2F", "/");
          void goto(resolve(`/[...path]?${query}`, { path: here }), {
            reset: false,
          });
        }}
      />
    </div>
    {#if !question}
      <div
        class="flex items-center gap-2.5"
        role="group"
        aria-labelledby="match-label"
      >
        <span id="match-label" class="text-sm font-semibold"
          >{m.match_label()}</span
        >
        <div class="flex">
          {#each matches as option (option.value)}
            <a
              class={cn(
                "-ms-px flex keycap items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                {
                  "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                    option.value === match,
                },
              )}
              href={resolve(`/[...path]?${option.query}`, { path: here })}
              aria-current={option.value === match ? "true" : undefined}
              data-sveltekit-reset="false">{option.label}</a
            >
          {/each}
        </div>
      </div>
    {/if}
  </div>

  <div
    class={cn(
      "@container col-span-12 grid content-start gap-1 bg-card px-1.5 pbs-2 pbe-2.5",
      { "lg:col-span-8": chamber },
    )}
  >
    <div class="overflow-x-auto">
      <table class="border-collapse text-sm/tight inline-full">
        <thead>
          <tr class="border-be text-[0.8125rem] text-muted-foreground">
            <th class="px-2 py-1.5 text-start font-medium" scope="col"
              >{m.col_option()}</th
            >
            <th
              class={cn("px-2 py-1.5 text-end font-medium", {
                "max-sm:hidden": contest.target != null,
              })}
              scope="col">{m.col_votes()}</th
            >
            <th class="px-2 py-1.5 text-end font-medium" scope="col"
              >{m.col_share()}</th
            >
            {#if seated}
              <th class="px-2 py-1.5 text-end font-medium" scope="col"
                >{electoral ? m.col_electors() : m.col_seats()}</th
              >
            {/if}
            {#if contest.target != null}
              <th class="px-2 py-1.5 text-end font-medium" scope="col"
                >{m.col_earlier({ year })}</th
              >
              <th
                class="px-2 py-1.5 text-end font-medium inline-[28%] max-md:inline-auto"
                scope="col">{m.col_change()}</th
              >
            {/if}
          </tr>
        </thead>
        <tbody>
          {#each shown as row (row.key)}
            <tr
              class={cn("border-be border-border/60", {
                "text-muted-foreground": row.current == null,
                "animate-in fade-in motion-reduce:animate-none": folded.has(
                  row.key,
                ),
              })}
              style:--party={swatch(row.color, row.key)}
            >
              <th class="px-2 py-1.5 text-start font-normal" scope="row">
                <span class="flex items-baseline gap-2">
                  <span
                    class="shrink-0 self-center rounded-xs bg-(--party) block-2.5 inline-2.5"
                  ></span>
                  <span class="min-inline-0">
                    <span class="font-semibold text-foreground"
                      >{row.label}</span
                    >
                    {#if row.short != null && row.short !== row.label}
                      <span class="ms-1 text-[0.8125rem] text-muted-foreground"
                        >{row.short}</span
                      >
                    {/if}
                    {#if row.current?.outcome != null && row.current.seats == null}
                      <span
                        class="ms-1 highlighter px-1 text-xs font-semibold text-foreground"
                        >{OUTCOMES[row.current.outcome]?.() ??
                          row.current.outcome}</span
                      >
                    {/if}
                    {#if row.backed != null}
                      <span class="block text-[0.8125rem] text-muted-foreground"
                        >{m.backed({ name: row.backed })}</span
                      >
                    {/if}
                    {#if row.origin != null}
                      <span class="block text-[0.8125rem] text-muted-foreground"
                        >{m.split_from({ name: row.origin })}</span
                      >
                    {/if}
                  </span>
                </span>
              </th>
              <td
                class={cn(
                  "px-2 py-1.5 text-end font-mono text-[0.8125rem] whitespace-nowrap tabular-nums",
                  { "max-sm:hidden": contest.target != null },
                )}
              >
                {row.current == null ? "" : count(row.current.votes)}
              </td>
              {#if row.current == null}
                <td
                  class="px-2 py-1.5 text-end text-[0.8125rem] whitespace-nowrap italic"
                  colspan={seated ? 2 : 1}>{m.didnt_run()}</td
                >
              {:else}
                <td
                  class="relative px-2 py-1.5 text-end font-mono text-[0.8125rem] whitespace-nowrap tabular-nums"
                >
                  {percent(row.current.share)}
                  <span
                    class="absolute inset-e-2 inset-be-0.5 bg-(--party) block-0.5"
                    style:width="calc({(row.current.share / topShare) * 100}% -
                    1rem)"
                  ></span>
                </td>
                {#if seated}
                  <td
                    class="px-2 py-1.5 text-end font-mono text-[0.8125rem] font-semibold tabular-nums"
                    >{row.current.seats ?? ""}</td
                  >
                {/if}
              {/if}
              {#if contest.target != null}
                <td
                  class="px-2 py-1.5 text-end font-mono text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums"
                >
                  {#if row.earlier == null}
                    <span class="font-sans font-semibold text-foreground"
                      >{m.new_option()}</span
                    >
                  {:else}
                    {percent(row.earlier.share)}
                  {/if}
                </td>
                <td class="px-2 py-1.5">
                  {#if row.delta != null}
                    <span class="flex items-center justify-end gap-2">
                      <span
                        class="relative flex-1 block-2.5 min-inline-16 max-md:hidden"
                        aria-hidden="true"
                      >
                        <span
                          class="absolute inset-y-[-3px] inset-s-1/2 bg-border inline-px"
                        ></span>
                        <span
                          class="absolute inset-y-0 rounded-xs bg-(--party)"
                          style:inset-inline-start={row.delta >= 0
                            ? "50%"
                            : null}
                          style:inset-inline-end={row.delta < 0 ? "50%" : null}
                          style:width="{(Math.abs(row.delta) / topDelta) * 50}%"
                        ></span>
                      </span>
                      <span
                        class="shrink-0 text-end font-mono text-[0.8125rem] font-semibold tabular-nums inline-14"
                        >{points(row.delta)}</span
                      >
                    </span>
                  {/if}
                </td>
              {/if}
            </tr>
          {/each}
          {#if folded.size > 0}
            <tr>
              <th class="px-2 py-1.5 text-start font-normal" scope="row">
                <button
                  type="button"
                  class="flex keycap items-center rounded-sm border border-input bg-card px-2 text-[0.8125rem] font-semibold block-7 hover:bg-secondary"
                  aria-expanded={expanded}
                  onclick={() => (expanded = !expanded)}
                  >{expanded
                    ? m.results_fewer()
                    : m.results_more({ count: folded.size })}</button
                >
              </th>
              {#if !expanded}
                <td
                  class={cn(
                    "px-2 py-1.5 text-end font-mono text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums",
                    { "max-sm:hidden": contest.target != null },
                  )}>{count(tail.votes)}</td
                >
                <td
                  class="px-2 py-1.5 text-end font-mono text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums"
                  >{percent(tail.share)}</td
                >
              {/if}
            </tr>
          {/if}
        </tbody>
      </table>
    </div>
    {#if captions.length > 0}
      <p class="px-2 text-[0.8125rem] text-muted-foreground">
        <a
          class="underline decoration-border underline-offset-4 hover:decoration-foreground"
          href="#sources">{captions.join("; ")}</a
        >
      </p>
    {/if}
  </div>

  {#if chamber}
    <Seats {parties} />
  {/if}
{/if}

{#if contest.places != null}
  {#key here}
    <Places
      places={contest.places}
      comparing={contest.target != null}
      {publishers}
    />
  {/key}
{:else if contest.children.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.children_title()}</h2>
    <ul class="columns-2 gap-6 text-sm md:columns-4">
      {#each contest.children as child, index (index)}
        <li class="break-inside-avoid py-0.5">
          {#if child.path == null}
            {child.name}
          {:else}
            <a
              class="hover:highlighter"
              href={resolve("/[...path]", { path: child.path })}>{child.name}</a
            >
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{/if}

{#if notes.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3 md:col-span-6">
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.notes_title()}</h2>
    <ul class="grid gap-1.5 text-sm/snug text-muted-foreground">
      {#each notes as note, index (index)}
        <li class="max-inline-[72ch]">{note}</li>
      {/each}
    </ul>
  </section>
{/if}

{#if contest.sets.length > 0 || contest.citations.length > 0 || checks.length > 0}
  <section
    id="sources"
    class={cn("col-span-12 grid content-start gap-4 bg-card px-3.5 py-3", {
      "md:col-span-6": notes.length > 0,
    })}
  >
    {#if contest.sets.length > 0}
      <div>
        <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.sources_title()}</h2>
        <ul class="grid gap-2.5 text-sm/snug">
          {#each contest.sets as set, index (index)}
            <li class="grid gap-0.5">
              <span class="flex flex-wrap items-baseline gap-x-2">
                <span class="font-semibold"
                  >{STAGES[set.stage]?.() ?? set.stage}</span
                >
                {#if set.isDefault}
                  <span class="highlighter px-1 text-xs font-semibold"
                    >{m.source_shown()}</span
                  >
                {/if}
                {#if set.status !== "active"}
                  <span class="text-xs font-semibold text-destructive"
                    >{STATUSES[set.status]?.() ?? set.status}</span
                  >
                {/if}
                {#if set.completeness != null && set.completeness < 1}
                  <span class="text-xs text-muted-foreground"
                    >{m.source_completeness({
                      percent: percent(set.completeness),
                    })}</span
                  >
                {/if}
              </span>
              <span>
                {set.source.publisher}:
                {#if set.source.url == null}
                  {set.source.title}
                {:else}
                  <a
                    class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                    href={set.source.url}
                    rel="external noopener">{set.source.title}</a
                  >
                {/if}
              </span>
              {#if set.source.license != null}
                <span class="text-[0.8125rem] text-muted-foreground"
                  >{m.source_license({ license: set.source.license })}</span
                >
              {/if}
            </li>
          {/each}
        </ul>
      </div>
    {/if}
    {#if contest.citations.length > 0}
      <div>
        <h2 class="mbe-1.5 text-[0.8125rem] font-bold">
          {m.citations_title()}
        </h2>
        <ul class="grid gap-1.5 text-sm/snug">
          {#each contest.citations as citation, index (index)}
            <li>
              {citation.publisher}:
              {#if citation.url == null}
                {citation.title}
              {:else}
                <a
                  class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                  href={citation.url}
                  rel="external noopener">{citation.title}</a
                >
              {/if}
            </li>
          {/each}
        </ul>
      </div>
    {/if}
    {#if checks.length > 0}
      <div>
        <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.checks_title()}</h2>
        <ul class="grid gap-1.5 text-sm/snug">
          {#each checks as check (check.code)}
            <li>
              <details class="group">
                <summary
                  class="cursor-pointer marker:text-muted-foreground hover:highlighter"
                  >{check.summary}</summary
                >
                <ul
                  class="mbs-1.5 grid gap-1 overflow-y-auto border-s ps-3 text-[0.8125rem] text-muted-foreground max-block-64"
                >
                  {#each check.units as unit, index (index)}
                    <li>{unit}</li>
                  {/each}
                </ul>
              </details>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  </section>
{/if}
