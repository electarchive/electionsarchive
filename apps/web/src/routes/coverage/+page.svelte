<script lang="ts">
  import { goto, invalidateAll } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { count, percent } from "#lib/format.js";
  import { cn } from "#lib/utils.js";
  import { m } from "#lib/paraglide/messages.js";
  import { Switch } from "#lib/components/ui/switch/index.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import type { PageData } from "./$types";
  import type { CountryTier, Tier } from "#lib/coverage.js";
  import { COUNTRY_TIERS, TIER_LABELS } from "#lib/coverage.js";
  import Tone from "./tone.svelte";

  const { data }: { data: PageData } = $props();

  const year = String(new Date().getFullYear());
  const columns: Partial<Record<Tier, CountryTier>> = {
    federal: "national",
    statewide: "subnational",
    legislative: "subnational",
  };

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
</script>

<svelte:head>
  <title>{m.coverage_overview_title()} | {m.wordmark()}</title>
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="pbe-16">
  <article class="grid gap-px overflow-clip border-be bg-border">
    <header class="grid gap-2 bg-card px-3.5 pbs-4 pbe-3.5">
      <h1 class="title">{m.coverage_overview_title()}</h1>
      <p class="text-[0.9375rem] text-muted-foreground max-inline-[72ch]">
        {m.coverage_overview_lede()}
      </p>
      {#if data.totals.first != null && data.totals.last != null}
        <p class="text-[0.9375rem] tabular-nums max-inline-[72ch]">
          {m.coverage_overview_totals({
            elections: count(data.totals.elections),
            contests: count(data.totals.contests),
            first: String(data.totals.first),
            last: String(data.totals.last),
          })}
        </p>
      {/if}
      <div class="grid gap-1 pbs-1">
        <label class="flex items-center gap-2 text-[0.8125rem] font-semibold">
          <Switch
            checked={!data.current}
            onCheckedChange={(value: boolean) =>
              void goto(
                value
                  ? resolve("/coverage")
                  : `${resolve("/coverage")}?current=1`,
                { replace: true, reset: false },
              )}
          />
          {m.coverage_current({ year })}
        </label>
        <p class="text-xs text-muted-foreground">
          {m.coverage_current_hint({ year })}
        </p>
      </div>
      <p class="text-xs text-muted-foreground">{built}</p>
      {#if data.stale}
        <p role="status" class="text-sm font-semibold text-(--no)">
          {m.coverage_stale()}
        </p>
      {/if}
    </header>
    <section aria-labelledby="coverage-countries" class="grid bg-card">
      <h2 id="coverage-countries" class="sr-only">
        {m.coverage_overview_column_country()}
      </h2>
      <p class="border-be px-3.5 py-2 text-xs text-muted-foreground">
        {m.coverage_overview_column_tiers()}.
        <a
          href="#coverage-explain"
          class="text-foreground underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"
          >{m.coverage_overview_how()}</a
        >
      </p>
      <div
        aria-hidden="true"
        class="hidden border-be px-3.5 py-1.5 text-xs/tight font-bold lg:grid lg:grid-cols-[minmax(0,11rem)_repeat(5,minmax(0,1fr))_6.5rem] lg:gap-x-5"
      >
        <span>{m.coverage_overview_column_country()}</span>
        {#each COUNTRY_TIERS as column (column)}
          <span>{TIER_LABELS[column]()}</span>
        {/each}
        <span>{m.coverage_overview_column_span()}</span>
      </div>
      <ul class="grid">
        {#each data.polities as polity (polity.polity)}
          <li
            class="relative grid gap-x-5 gap-y-2 border-be px-3.5 py-3 hover:bg-muted/60 lg:grid-cols-[minmax(0,11rem)_repeat(5,minmax(0,1fr))_6.5rem] lg:items-start"
          >
            <a
              href={resolve("/coverage/[cc]", { cc: polity.polity })}
              class="text-[0.9375rem] font-bold underline-offset-2 after:absolute after:inset-0 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
              >{polity.name}</a
            >
            <ul
              class="grid grid-cols-2 gap-x-4 gap-y-2.5 text-sm tabular-nums sm:grid-cols-3 lg:contents"
            >
              {#each COUNTRY_TIERS as column (column)}
                {const tiers = $derived(
                  polity.tiers.filter(
                    (tier) =>
                      (columns[tier.key as Tier] ?? tier.key) === column,
                  ),
                )}
                {#if tiers.length === 0}
                  <li aria-hidden="true" class="hidden lg:block">
                    <span class="text-xs text-muted-foreground">–</span>
                  </li>
                {:else}
                  <li class="grid content-start gap-2.5">
                    {#each tiers as tier (tier.key)}
                      <div class="grid content-start gap-0.5">
                        <span
                          class={cn("text-xs text-muted-foreground", {
                            "lg:sr-only": tier.key === column,
                          })}>{TIER_LABELS[tier.key as Tier]()}</span
                        >
                        {#if tier.cell == null || tier.cell.total === 0}
                          <span class="text-xs text-muted-foreground"
                            >{m.coverage_cell_none()}</span
                          >
                        {:else}
                          <span class="font-semibold"
                            >{percent(
                              tier.cell.complete / tier.cell.total,
                              1,
                            )}</span
                          >
                          <span
                            aria-hidden="true"
                            class="flex overflow-hidden rounded-xs bg-muted block-1 max-inline-28"
                          >
                            <span
                              class="block-full"
                              style:inline-size="{(tier.cell.complete /
                                tier.cell.total) *
                                100}%"
                            >
                              <Tone
                                reason="complete"
                                class="block block-full inline-full"
                              />
                            </span>
                          </span>
                          <span class="text-xs text-muted-foreground"
                            >{m.coverage_cell_of({
                              complete: count(tier.cell.complete),
                              total: count(tier.cell.total),
                            })}</span
                          >
                        {/if}
                      </div>
                    {/each}
                  </li>
                {/if}
              {/each}
            </ul>
            <p class="text-sm tabular-nums">
              <span class="text-xs text-muted-foreground lg:sr-only"
                >{m.coverage_overview_column_span()}:</span
              >
              {polity.first == null || polity.last == null
                ? ""
                : `${polity.first}–${polity.last}`}
            </p>
          </li>
        {/each}
      </ul>
    </section>
    <section
      aria-labelledby="coverage-explain"
      class="grid gap-2 bg-card px-3.5 pbs-4 pbe-4"
    >
      <h2 id="coverage-explain" class="text-lg font-bold">
        {m.coverage_overview_done()}
      </h2>
      <div class="grid gap-2 text-[0.9375rem] max-inline-[72ch]">
        <p>{m.coverage_overview_done_body()}</p>
        <p>{m.coverage_overview_explain_count()}</p>
        <p>{m.coverage_overview_explain_gaps()}</p>
      </div>
    </section>
  </article>
</main>
