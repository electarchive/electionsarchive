<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import { count, date, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

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
</script>

<header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
  <h1
    class="font-serif text-[clamp(1.75rem,3vw,2.5rem)] leading-[1.02] font-[640] tracking-[-0.01em] text-balance"
  >
    {contest.name}
  </h1>
  <p
    class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem] text-muted-foreground"
  >
    {#if contest.election.path == null}
      <span>{contest.election.name}</span>
    {:else}
      <a
        class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        href={resolve("/[...path]", { path: contest.election.path })}
        >{contest.election.name}</a
      >
    {/if}
    <time datetime={contest.election.date}>{date(contest.election.date)}</time>
    {#if contest.election.status === "annulled"}
      <span class="highlighter px-1 font-semibold text-foreground"
        >{m.election_annulled()}</span
      >
    {/if}
  </p>
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
          style:--party={row.color ?? "var(--muted-foreground)"}
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
          void goto(
            resolve(`/[...path]?${query}`, { path: contest.path ?? "" }),
            { reset: false },
          );
        }}
      />
    </div>
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
            href={resolve(`/[...path]?${option.query}`, {
              path: contest.path ?? "",
            })}
            aria-current={option.value === match ? "true" : undefined}
            data-sveltekit-reset="false">{option.label}</a
          >
        {/each}
      </div>
    </div>
  </div>

  <div class="@container col-span-12 overflow-x-auto bg-card px-1.5 py-2">
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
              >{m.col_seats()}</th
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
        {#each contest.rows as row (row.key)}
          <tr
            class={cn("border-be border-border/60 last:border-be-0", {
              "text-muted-foreground": row.current == null,
            })}
            style:--party={row.color ?? "var(--muted-foreground)"}
          >
            <th class="px-2 py-1.5 text-start font-normal" scope="row">
              <span class="flex items-baseline gap-2">
                <span
                  class="shrink-0 self-center rounded-xs bg-(--party) block-2.5 inline-2.5"
                ></span>
                <span class="min-inline-0">
                  <span class="font-semibold text-foreground">{row.label}</span>
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
                        style:inset-inline-start={row.delta >= 0 ? "50%" : null}
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
      </tbody>
    </table>
  </div>
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

{#if contest.sets.length > 0}
  <section
    class={cn("col-span-12 bg-card px-3.5 py-3", {
      "md:col-span-6": notes.length > 0,
    })}
  >
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
  </section>
{/if}

{#if contest.children.length > 0}
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
