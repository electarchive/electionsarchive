<script lang="ts">
  import { page } from "$app/state";
  import BountyStatus from "#lib/components/contrib/bounty-status.svelte";
  import FilterNav from "#lib/components/contrib/filter-nav.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { CATEGORY_LABELS, GRAIN_LABELS } from "#lib/coverage.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const kept = $derived(
    [...page.url.searchParams].filter(
      ([key]) => key !== "year" && key !== "after",
    ),
  );
  const year = $derived(page.url.searchParams.get("year") ?? "");
  const more = $derived.by(() => {
    const search = new URLSearchParams(page.url.search);
    search.set("after", String(data.next));
    return `/bounties?${search}`;
  });
  const twin = $derived(`/bounties.md${page.url.search}`);
</script>

<svelte:head>
  <title>{m.bounties_title()} | {m.wordmark()}</title>
  <meta name="description" content={m.bounties_lede()} />
  <link
    rel="alternate"
    type="text/markdown"
    title={m.bounties_title()}
    href={twin}
  />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <h1 class="title">{m.bounties_heading()}</h1>
    <p class="max-inline-prose text-sm text-pretty text-muted-foreground">
      {m.bounties_lede()}
    </p>
    <p class="max-inline-prose text-sm text-pretty text-muted-foreground">
      {m.bounties_blocked_lede()}
    </p>
    <div class="grid gap-1.5 pbs-1">
      <FilterNav
        label={m.bounties_filter_status()}
        name="status"
        options={[
          { value: null, label: m.bounties_status_open() },
          { value: "fulfilled", label: m.bounties_status_fulfilled() },
          { value: "all", label: m.bounties_filter_all() },
        ]}
      />
      <FilterNav
        label={m.bounties_filter_category()}
        name="category"
        options={[
          { value: null, label: m.bounties_filter_all() },
          { value: "blocked", label: CATEGORY_LABELS.blocked() },
          { value: "known_gap", label: CATEGORY_LABELS.known_gap() },
        ]}
      />
      <FilterNav
        label={m.bounties_filter_place()}
        name="polity"
        options={[
          { value: null, label: m.bounties_filter_all() },
          ...data.polities.map((row) => ({
            value: row.value,
            label: `${row.name ?? row.value.toUpperCase()} (${row.count})`,
          })),
        ]}
      />
      <FilterNav
        label={m.bounties_filter_grain()}
        name="grain"
        options={[
          { value: null, label: m.bounties_filter_all() },
          ...data.grains.map((row) => ({
            value: row.value,
            label: `${GRAIN_LABELS[row.value]?.() ?? row.value} (${row.count})`,
          })),
        ]}
      />
      <form
        method="GET"
        class="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-x-3 text-sm"
      >
        {#each kept as [key, value] (key)}
          <input type="hidden" name={key} {value} />
        {/each}
        <label for="bounty-year" class="text-xs text-muted-foreground"
          >{m.bounties_filter_year()}</label
        >
        <span class="flex items-center gap-2">
          <input
            id="bounty-year"
            name="year"
            inputmode="numeric"
            pattern="\d{4}"
            maxlength="4"
            value={year}
            class="rounded-sm border border-input bg-transparent px-2 font-figures block-8 inline-22"
          />
          <Button type="submit" variant="outline" size="sm"
            >{m.bounties_filter_apply()}</Button
          >
        </span>
      </form>
    </div>
  </header>

  {#if data.bounties.length === 0}
    <p class="mbs-6 border-bs pbs-3 text-sm text-pretty text-muted-foreground">
      {m.bounties_empty()}
    </p>
  {:else}
    <ol class="mbs-6 grid border-bs-[3px] border-foreground">
      {#each data.bounties as bounty (bounty.id)}
        <li
          class="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-3 border-be py-3 max-sm:grid-cols-[2.75rem_minmax(0,1fr)]"
        >
          <span class="font-figures text-sm text-muted-foreground tabular-nums"
            >#{bounty.number}</span
          >
          <div class="grid gap-1 min-inline-0">
            <a
              class="font-semibold text-pretty underline-offset-4 hover:underline"
              href="/bounties/{bounty.number}">{bounty.label}</a
            >
            <p class="text-xs text-muted-foreground">
              {[
                bounty.place ?? bounty.polity.toUpperCase(),
                bounty.years === "" ? null : bounty.years.replace("-", "–"),
                GRAIN_LABELS[bounty.grain]?.() ?? bounty.grain,
                bounty.category === "blocked"
                  ? CATEGORY_LABELS.blocked()
                  : null,
              ]
                .filter((part) => part != null)
                .join(", ")}
            </p>
            <BountyStatus {bounty} />
          </div>
        </li>
      {/each}
    </ol>
  {/if}

  {#if data.next != null}
    <p class="mbs-4 text-sm">
      <a class="underline underline-offset-2" href={more}>{m.bounties_more()}</a
      >
    </p>
  {/if}

  <p class="mbs-10 border-bs pbs-3 text-sm text-pretty text-muted-foreground">
    {m.bounties_footer()}
    <a class="underline underline-offset-2" href="/coverage"
      >{m.bounties_coverage_link()}</a
    >
  </p>
</main>
