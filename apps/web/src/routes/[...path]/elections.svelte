<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { SvelteSet } from "svelte/reactivity";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { capitalize, count, date } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";
  import Preview from "./preview.svelte";

  const {
    elections,
    place,
    heading,
  }: {
    elections: NonNullable<
      Extract<PageData, { kind: "polity" }>["polity"]["elections"]["loaded"]
    >["value"];
    place: string;
    heading: string;
  } = $props();

  const STATUSES: Record<string, () => string> = {
    scheduled: m.election_scheduled,
    postponed: m.election_postponed,
    canceled: m.election_canceled,
    annulled: m.election_annulled,
  };
  const FILTERS: Record<string, () => string> = {
    all: m.polity_filter_all,
    general: m.polity_filter_general,
    primary: m.polity_filter_primary,
    runoff: m.polity_filter_runoff,
    other: m.polity_filter_other,
  };

  let filter = $state(page.url.searchParams.get("show") ?? "all");
  const opened = new SvelteSet<string>();
  let isAllElections = $state(false);

  $effect(() => {
    keep({ show: filter === "all" ? null : filter });
  });

  const sorts = $derived(
    elections.map((election) => ({
      ...election,
      sort:
        election.kind === "general"
          ? "general"
          : election.kind === "primary" || election.kind === "caucus"
            ? "primary"
            : election.kind === "runoff"
              ? "runoff"
              : "other",
    })),
  );
  const present = $derived(
    Object.keys(FILTERS).filter(
      (key) => key === "all" || sorts.some((election) => election.sort === key),
    ),
  );
  const filterOptions = $derived(
    present.map((key) => ({ value: key, label: FILTERS[key]?.() ?? key })),
  );
  const years = $derived(
    Map.groupBy(
      sorts.filter((election) => filter === "all" || election.sort === filter),
      (election) => election.year,
    )
      .entries()
      .toArray(),
  );
</script>

<div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
  <h2 id={heading} class="text-[0.8125rem] font-bold">
    {m.polity_elections()}
  </h2>
  {#if present.length > 2}
    <Segmented
      options={filterOptions}
      bind:value={filter}
      label={m.polity_filter_label()}
    />
  {/if}
</div>
{#if elections.length === 0}
  <p class="text-sm text-muted-foreground">{m.polity_no_elections()}</p>
{:else if years.length === 0}
  <p class="text-sm text-muted-foreground">{m.polity_none_in_filter()}</p>
{:else}
  <ol class="grid">
    {#each isAllElections ? years : years.slice(0, 12) as [year, entries] (year)}
      <li
        class="grid grid-cols-[4.5rem_minmax(0,1fr)] items-baseline gap-x-3 border-be border-border/60 py-1.5 last:border-be-0"
      >
        <span
          class="font-figures text-[0.9375rem] tracking-[-0.02em] tabular-nums"
          >{year ?? ""}</span
        >
        {const isOpenYear = $derived(opened.has(`year:${year}`))}
        <ul class="grid gap-1 text-sm">
          {#each isOpenYear || entries.length <= 6 ? entries : entries.slice(0, 5) as election, index (index)}
            {const dated = $derived(
              election.name.replace(
                new RegExp(`^(?:\\p{L}+ (?:\\d{1,2}, )?)?${year} `, "u"),
                "",
              ),
            )}
            {const short = $derived(
              dated.startsWith(`${place} `)
                ? dated.slice(place.length + 1)
                : dated,
            )}
            {const label = $derived(
              election.lead == null
                ? capitalize(short)
                : election.lead.more > 0
                  ? m.polity_local_more({
                      place: election.lead.place,
                      count: count(election.lead.more),
                    })
                  : m.polity_local_one({ place: election.lead.place }),
            )}
            <li
              class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3"
            >
              <span class="flex flex-wrap items-baseline gap-x-2">
                {#if election.path == null}
                  <span>{label}</span>
                {:else if election.scope != null}
                  <a
                    class="hover:highlighter"
                    href="{resolve('/[...path]', {
                      path: election.path,
                    })}?in={election.scope}">{label}</a
                  >
                {:else if election.isCounting}
                  <a
                    class="hover:highlighter"
                    href={resolve("/live/[...path]", { path: election.path })}
                    >{label}</a
                  >
                  <span
                    class="inline-flex items-center gap-1.5 text-xs font-semibold text-destructive"
                    ><span
                      class="rounded-full bg-destructive block-1.5 inline-1.5"
                      aria-hidden="true"
                    ></span>{m.polity_counting_short()}</span
                  >
                {:else}
                  <Preview class="hover:highlighter" path={election.path}
                    >{label}</Preview
                  >
                {/if}
                {#if !election.isCounting && STATUSES[election.status] != null}
                  <span class="highlighter px-1 text-xs font-semibold"
                    >{STATUSES[election.status]?.()}</span
                  >
                {/if}
              </span>
              <time
                class="text-xs text-muted-foreground tabular-nums"
                datetime={election.date}>{date(election.date)}</time
              >
            </li>
          {/each}
          {#if entries.length > 6}
            <li>
              <button
                type="button"
                class="text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
                aria-expanded={isOpenYear}
                onclick={() => {
                  if (isOpenYear) {
                    opened.delete(`year:${year}`);
                  } else {
                    opened.add(`year:${year}`);
                  }
                }}
                >{isOpenYear
                  ? m.regions_fewer()
                  : m.regions_more({ count: count(entries.length) })}</button
              >
            </li>
          {/if}
        </ul>
      </li>
    {/each}
  </ol>
  {#if years.length > 12}
    <button
      type="button"
      class="justify-self-start rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary"
      aria-expanded={isAllElections}
      onclick={() => (isAllElections = !isAllElections)}
      >{isAllElections
        ? m.polity_results_fewer()
        : m.polity_results_more({ count: String(years.length) })}</button
    >
  {/if}
{/if}
