<script lang="ts">
  import { goto, invalidateAll } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { Switch } from "#lib/components/ui/switch/index.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import type { PageData } from "./$types";
  import Bar from "./bar.svelte";

  const { data }: { data: PageData } = $props();

  const year = String(new Date().getFullYear());
  const rows = $derived(
    data.polities.map((polity) => ({
      polity,
      lead:
        polity.goals.find(
          (row) =>
            row.goal === (polity.polity === "us" ? "county" : "national"),
        ) ?? null,
      shapes: polity.goals.find((row) => row.goal === "shapes") ?? null,
    })),
  );

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
    </header>
    <section aria-labelledby="coverage-countries" class="grid bg-card">
      <h2 id="coverage-countries" class="sr-only">
        {m.coverage_overview_column_country()}
      </h2>
      <div
        aria-hidden="true"
        class="hidden border-be px-3.5 py-1.5 text-xs/tight font-bold lg:grid lg:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_6.5rem_9rem_7rem] lg:gap-x-5"
      >
        <span>{m.coverage_overview_column_country()}</span>
        <span>{m.coverage_overview_column_goal()}</span>
        <span>{m.coverage_overview_column_span()}</span>
        <span>{m.coverage_overview_column_finest()}</span>
        <span class="text-end">{m.coverage_overview_column_shapes()}</span>
      </div>
      <ul class="grid">
        {#each rows as { polity, lead, shapes } (polity.polity)}
          <li
            class="relative grid gap-x-5 gap-y-2 border-be px-3.5 py-3 hover:bg-muted/60 lg:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_6.5rem_9rem_7rem] lg:items-center"
          >
            <a
              href={resolve("/coverage/[cc]", { cc: polity.polity })}
              class="text-[0.9375rem] font-bold underline-offset-2 after:absolute after:inset-0 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
              >{polity.name}</a
            >
            <div class="grid gap-1.5 min-inline-0">
              {#if lead != null}
                <p
                  class="flex flex-wrap items-baseline gap-x-2 text-sm tabular-nums"
                >
                  <span class="font-semibold"
                    >{m.coverage_progress({
                      complete: count(lead.complete),
                      total: count(lead.total),
                    })}</span
                  >
                  <span class="text-muted-foreground"
                    >{m.coverage_percent({
                      percent: percent(
                        lead.complete / Math.max(1, lead.total),
                        1,
                      ),
                    })}</span
                  >
                </p>
                <Bar
                  counts={lead}
                  goal={lead.goal}
                  polity={polity.polity}
                  class="relative z-1 block-2"
                />
                <p class="text-xs text-muted-foreground">
                  {(polity.polity === "us"
                    ? m.coverage_overview_goal_us
                    : m.coverage_overview_goal_national)()}
                </p>
              {:else}
                <p class="text-sm text-muted-foreground">
                  {m.coverage_overview_none()}
                </p>
              {/if}
            </div>
            <dl
              class="grid grid-cols-3 gap-x-4 text-sm tabular-nums lg:contents"
            >
              <div class="grid content-start gap-0.5">
                <dt class="text-xs text-muted-foreground lg:sr-only">
                  {m.coverage_overview_column_span()}
                </dt>
                <dd>
                  {polity.first == null || polity.last == null
                    ? ""
                    : `${polity.first}–${polity.last}`}
                </dd>
              </div>
              <div class="grid content-start gap-0.5">
                <dt class="text-xs text-muted-foreground lg:sr-only">
                  {m.coverage_overview_column_finest()}
                </dt>
                <dd class="first-letter:uppercase">
                  {polity.finestNoun ?? ""}
                </dd>
              </div>
              <div class="grid content-start gap-0.5 lg:text-end">
                <dt class="text-xs text-muted-foreground lg:sr-only">
                  {m.coverage_overview_column_shapes()}
                </dt>
                <dd>
                  {shapes == null
                    ? m.coverage_overview_shapes_none()
                    : m.coverage_overview_shapes({
                        percent: percent(
                          shapes.complete / Math.max(1, shapes.total),
                          1,
                        ),
                      })}
                </dd>
              </div>
            </dl>
          </li>
        {/each}
      </ul>
    </section>
    <section
      aria-labelledby="coverage-explain"
      class="grid gap-2 bg-card px-3.5 pbs-4 pbe-4"
    >
      <h2 id="coverage-explain" class="text-lg font-bold">
        {m.coverage_overview_explain()}
      </h2>
      <div class="grid gap-2 text-[0.9375rem] max-inline-[72ch]">
        <p>{m.coverage_overview_explain_count()}</p>
        <p>{m.coverage_overview_explain_goal()}</p>
        <p>{m.coverage_overview_explain_gaps()}</p>
        <p>{m.coverage_overview_explain_shapes()}</p>
      </div>
    </section>
  </article>
</main>
