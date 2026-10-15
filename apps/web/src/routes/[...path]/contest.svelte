<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, date, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Changes from "./changes.svelte";
  import Electors from "./electors.svelte";
  import PlaceSwitcher from "./place-switcher.svelte";
  import Places from "./places.svelte";
  import Results, { OUTCOMES } from "./results.svelte";
  import Seats from "./seats.svelte";

  const {
    contest,
    match,
  }: {
    contest: Extract<PageData, { kind: "contest" }>["contest"];
    match: string;
  } = $props();

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
  const MEASURES: Record<string, () => string> = {
    list_votes: m.measure_list_votes,
    electoral_votes: m.measure_electoral_votes,
    state_delegate_equivalents: m.measure_state_delegate_equivalents,
  };
  const SEATS: Record<string, () => string> = {
    office: m.col_electors,
    elector_slate: m.col_electors,
    delegate_allocation: m.col_delegates,
  };

  const tether = TooltipPrimitive.createTether<string>();

  let filter = $state<{ at: string; value: number | string | null }>({
    at: "",
    value: null,
  });

  const here = $derived(page.url.pathname.slice(1));
  const only = $derived(filter.at === page.url.href ? filter.value : null);
  const drilled = $derived(contest.scope.noun != null);
  const counted = $derived(MEASURES[contest.current?.measure ?? ""]?.());
  const question = $derived(
    contest.rows.some((row) => row.key.startsWith("answer:")),
  );
  const seated = $derived.by(() => {
    let total = 0;
    for (const row of contest.rows) {
      total += row.current?.seats ?? 0;
    }
    return total > 1;
  });
  const phrases = $derived.by(() => {
    const mine = contest.name.replace(/^\d{4}\s+/u, "").split(" ");
    return new Map(
      contest.targets.map((target) => {
        const theirs = target.label.replace(/^\d{4}\s+/u, "").split(" ");
        const shared = Math.min(mine.length, theirs.length);
        let start = 0;
        while (start < shared && mine[start] === theirs[start]) {
          start += 1;
        }
        let end = 0;
        while (
          end < shared - start &&
          mine.at(-1 - end) === theirs.at(-1 - end)
        ) {
          end += 1;
        }
        const phrase = theirs.slice(start, theirs.length - end).join(" ");
        return [target.value, { phrase, whole: theirs.join(" ") }] as const;
      }),
    );
  });
  const span = $derived.by(() => {
    const target = contest.target?.date;
    const own = contest.election.date;
    if (contest.target == null || target == null || own == null) {
      return null;
    }
    const race = contest.comparison.notes.some(
      (note) => note.code === "different_race",
    )
      ? (phrases.get(contest.target.path ?? "")?.phrase ?? "")
      : "";
    const label = (value: string) =>
      value.slice(0, 4) === target.slice(0, 4) &&
      value.slice(0, 4) === own.slice(0, 4)
        ? new Intl.DateTimeFormat(getLocale(), {
            month: "short",
            year: "numeric",
            timeZone: "UTC",
          }).format(new Date(`${value}T00:00:00Z`))
        : value.slice(0, 4);
    const forward = target > own;
    const [from, to] = forward ? [own, target] : [target, own];
    return {
      from: label(from),
      to: label(to),
      forward,
      race: race === "" ? null : race,
    };
  });
  const lead = $derived.by(() => {
    const [top, next] = contest.rows
      .filter((row) => row.current != null && !row.key.startsWith("kind:"))
      .toSorted((a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0));
    return top == null || next == null
      ? null
      : {
          row: top,
          margin: (top.current?.share ?? 0) - (next.current?.share ?? 0),
        };
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
    return (
      (contest.kind === "body_seats" || contest.kind === "list_allocation") &&
      parties.length > 1 &&
      total >= 15
    );
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
  const items = $derived.by(() => {
    const winners = new Map(
      contest.series.map((entry) => [entry.path, entry.winner]),
    );
    const day = new Intl.DateTimeFormat(getLocale(), {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
    return [
      {
        value: "none",
        label: m.compare_none(),
        year: null,
        phrase: "",
        when: "",
        winner: null,
        annulled: false,
      },
      ...contest.targets
        .toSorted((a, b) => Number(b.series) - Number(a.series))
        .map((target) => {
          const winner = target.series
            ? (winners.get(target.value) ?? null)
            : null;
          const words = phrases.get(target.value);
          return {
            value: target.value,
            label: target.label,
            year: target.detail?.slice(0, 4) ?? null,
            phrase:
              target.series || words == null || words.phrase === ""
                ? (words?.whole ?? target.label)
                : words.phrase,
            when:
              target.detail == null
                ? ""
                : day.format(new Date(`${target.detail}T00:00:00Z`)),
            winner,
            annulled: target.annulled,
            group: target.series ? m.compare_series() : m.compare_other(),
            keywords: [target.detail ?? "", winner?.label ?? ""],
          };
        }),
    ];
  });
  const matches = $derived(
    [
      {
        value: "party",
        label: m.match_party(),
        detail: m.match_party_detail(),
      },
      {
        value: "person",
        label: m.match_person(),
        detail: m.match_person_detail(),
      },
      {
        value: "family",
        label: m.match_family(),
        detail: m.match_family_detail(),
      },
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
    ...contest.comparison.notes
      .filter((note) => span == null || note.code !== "different_race")
      .map((note) => {
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
      class={cn(
        "-mx-3.5 flex scrollbar-thin flex-row-reverse overflow-x-auto px-3.5 md:mx-0 md:px-0",
        { "md:col-span-2": years.length > 8 },
      )}
      aria-label={m.series_label()}
      {@attach (strip: HTMLElement) => {
        const current =
          strip.querySelectorAll("li")[
            years.findIndex((entry) => entry.current)
          ];
        void document.fonts.ready.then(() => {
          if (current != null) {
            const box = current.getBoundingClientRect();
            strip.scrollLeft +=
              box.left -
              strip.getBoundingClientRect().left -
              (strip.clientWidth - box.width) / 2;
          }
        });
      }}
    >
      <ol class="me-auto flex inline-max">
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
  {#if contest.electors != null}
    <Electors electors={contest.electors} />
  {/if}
  <dl class="col-span-12 grid grid-cols-12 gap-px">
    <div class="col-span-6 bg-card px-3.5 py-3 md:col-span-4">
      <dt class="text-[0.8125rem] text-muted-foreground">
        {counted ?? m.stat_valid()}
      </dt>
      <dd class="mbs-0.5 font-mono text-2xl tracking-[-0.02em] tabular-nums">
        {count(contest.current.valid)}
      </dd>
    </div>
    <div class="col-span-6 bg-card px-3.5 py-3 md:col-span-4">
      <dt class="text-[0.8125rem] text-muted-foreground">
        {m.stat_options()}
      </dt>
      <dd class="mbs-0.5 font-mono text-2xl tracking-[-0.02em] tabular-nums">
        {contest.rows.filter((row) => row.current != null).length}
      </dd>
    </div>
    <div class="col-span-12 bg-card px-3.5 py-3 md:col-span-4">
      {#if lead != null}
        <dt class="text-[0.8125rem] text-muted-foreground">
          {m.col_margin()}
        </dt>
        <dd
          class="mbs-0.5 flex items-baseline gap-2.5 min-inline-0"
          style:--party={swatch(lead.row.color, lead.row.key)}
        >
          <span class="font-mono text-2xl tracking-[-0.02em] tabular-nums"
            >{points(lead.margin)}</span
          >
          <span class="flex items-center gap-1.5 truncate text-sm min-inline-0">
            <span class="shrink-0 rounded-xs bg-(--party) block-2.5 inline-2.5"
            ></span>
            <span class="truncate font-semibold">{lead.row.label}</span>
          </span>
        </dd>
      {/if}
    </div>
  </dl>

  <div
    class="col-span-12 flex flex-wrap items-center gap-x-6 gap-y-3 bg-card px-3.5 py-3"
  >
    <div
      class="flex flex-1 basis-80 flex-wrap items-center gap-x-2.5 gap-y-1.5 min-inline-0"
    >
      <span class="shrink-0 text-sm font-semibold max-sm:basis-full"
        >{m.compare_label()}</span
      >
      <Combobox
        class="flex-1 max-inline-md"
        wide
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
      >
        {#snippet row(item: (typeof items)[number])}
          {#if item.year == null}
            <span class="flex-1 min-inline-0">{item.label}</span>
          {:else}
            <span
              class={cn(
                "shrink-0 self-start font-mono font-semibold tabular-nums inline-10",
                { "line-through": item.annulled },
              )}>{item.year}</span
            >
            <span
              class="flex flex-1 flex-wrap items-baseline gap-x-1.5 min-inline-0"
            >
              {#if item.winner == null}
                <span class="text-pretty">{item.phrase}</span>
              {:else}
                <span class="flex items-baseline gap-1.5">
                  <span
                    class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
                    style:background-color={swatch(
                      item.winner.color,
                      item.winner.key,
                    )}
                  ></span>
                  <span class="text-pretty">{item.winner.label}</span>
                </span>
                <span
                  class="font-mono text-xs text-muted-foreground tabular-nums"
                  >{item.winner.measure === "electoral_votes"
                    ? m.series_electoral({
                        votes: count(item.winner.votes),
                        total: count(item.winner.total),
                      })
                    : percent(item.winner.share)}</span
                >
              {/if}
              {#if item.annulled}
                <span class="highlighter px-1 text-xs font-semibold"
                  >{m.election_annulled()}</span
                >
              {/if}
            </span>
            <span
              class="shrink-0 self-start font-mono text-xs/5 text-muted-foreground tabular-nums"
              >{item.when}</span
            >
          {/if}
        {/snippet}
        {#snippet selection(item: (typeof items)[number])}
          {#if item.year == null}
            <span class="flex-1 truncate min-inline-0">{item.label}</span>
          {:else}
            <span
              class="flex flex-1 items-baseline gap-2 min-inline-0"
              title={item.label}
            >
              <span class="shrink-0 font-mono font-semibold tabular-nums"
                >{item.year}</span
              >
              {#if item.winner == null}
                <span class="truncate">{item.phrase}</span>
              {:else}
                <span
                  class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
                  style:background-color={swatch(
                    item.winner.color,
                    item.winner.key,
                  )}
                ></span>
                <span class="truncate">{item.winner.label}</span>
              {/if}
            </span>
          {/if}
        {/snippet}
      </Combobox>
      {#if contest.target != null}
        <Tooltip.Root disableCloseOnTriggerClick>
          <Tooltip.Trigger
            class="shrink-0 rounded-xs highlighter px-1 text-[0.8125rem] font-semibold"
          >
            {contest.comparison.label === "exact"
              ? m.quality_exact()
              : m.quality_estimated()}
          </Tooltip.Trigger>
          <Tooltip.Content
            collisionPadding={8}
            class="leading-snug max-inline-64"
          >
            {contest.comparison.label === "exact"
              ? m.quality_exact_detail()
              : m.quality_estimated_detail()}
          </Tooltip.Content>
        </Tooltip.Root>
      {/if}
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
          <Tooltip.Root disableCloseOnTriggerClick {tether} delayDuration={300}>
            {#snippet children({ payload })}
              {#each matches as option (option.value)}
                <Tooltip.Trigger {tether} payload={option.detail}>
                  {#snippet child({ props })}
                    <a
                      {...props}
                      type={undefined}
                      class={cn(
                        "-ms-px flex keycap items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                        {
                          "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                            option.value === match,
                        },
                      )}
                      href={resolve(`/[...path]?${option.query}`, {
                        path: here,
                      })}
                      aria-current={option.value === match ? "true" : undefined}
                      data-sveltekit-reset="false">{option.label}</a
                    >
                  {/snippet}
                </Tooltip.Trigger>
              {/each}
              <Tooltip.Content
                collisionPadding={8}
                class="leading-snug max-inline-64">{payload}</Tooltip.Content
              >
            {/snippet}
          </Tooltip.Root>
        </div>
      </div>
    {/if}
  </div>

  {#if span != null && contest.target != null}
    <Changes
      rows={contest.rows}
      {span}
      valid={span.forward
        ? { before: contest.current.valid, after: contest.target.valid }
        : { before: contest.target.valid, after: contest.current.valid }}
      counted={counted ?? m.stat_valid()}
      places={contest.places}
      picked={only}
      onpick={(value) => {
        filter = { at: page.url.href, value };
        if (value != null) {
          document.querySelector("#places-title")?.scrollIntoView();
        }
      }}
    />
  {/if}

  <div
    class={cn(
      "@container col-span-12 grid content-start gap-1 bg-card px-1.5 pbs-2 pbe-2.5",
      { "lg:col-span-8": chamber },
    )}
  >
    <Results
      rows={contest.rows}
      {span}
      votes={counted ?? m.col_votes()}
      seats={seated ? (SEATS[contest.kind]?.() ?? m.col_seats()) : null}
    />
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
      {span}
      {publishers}
      votes={counted ?? m.col_votes()}
      bind:only={
        () => only,
        (value: number | string | null) =>
          (filter = { at: page.url.href, value })
      }
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
