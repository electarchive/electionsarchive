<script lang="ts">
  import { page } from "$app/state";
  import { onMount } from "svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Rows from "./rows.svelte";

  const { data }: { data: PageData } = $props();

  const VIEWS = ["percent", "left", "done"] as const;
  let view = $state<(typeof VIEWS)[number]>("percent");
  let hideDone = $state(false);

  onMount(() => {
    try {
      const saved = localStorage.getItem("coverage-view");
      const found = VIEWS.find((option) => option === saved);
      if (found != null) {
        view = found;
      }
      hideDone = localStorage.getItem("coverage-hide-done") === "true";
    } catch {}
  });

  const summary = $derived.by(() => {
    const sums: Record<
      | "expected"
      | "missing"
      | "county"
      | "countyTotal"
      | "precinct"
      | "precinctTotal"
      | "shapes"
      | "shapesTotal",
      number | null
    > = {
      expected: null,
      missing: null,
      county: null,
      countyTotal: null,
      precinct: null,
      precinctTotal: null,
      shapes: null,
      shapesTotal: null,
    };
    let held = 0;
    for (const row of data.rows) {
      held += row.held;
      for (const key of Object.keys(sums) as Array<keyof typeof sums>) {
        const value = row[key];
        if (value != null) {
          sums[key] = (sums[key] ?? 0) + Math.max(0, value);
        }
      }
    }
    return {
      ...sums,
      path: "all",
      label: m.coverage_all_years(),
      kind: "total",
      held,
      governments: null,
      precincts: 0,
      shaped: 0,
      hasChildren: false,
      href: null,
      detail: null,
    };
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
  <title>{m.coverage_title()} | {m.wordmark()}</title>
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="pbe-16">
  <article class="grid gap-px overflow-clip border-be bg-border">
    <header class="grid gap-2 bg-card px-3.5 pbs-4 pbe-3.5">
      <h1 class="title">{m.coverage_title()}</h1>
      <p class="text-[0.9375rem] text-muted-foreground max-inline-[72ch]">
        {m.coverage_lede()}
      </p>
      <p class="text-sm text-muted-foreground max-inline-[72ch]">
        {m.coverage_denominator()}
      </p>
      <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div class="grid gap-1.5">
          <p class="text-xs text-muted-foreground">{built}</p>
          <p class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span class="text-muted-foreground">{m.coverage_legend()}</span>
            <span class="flex items-center gap-1"
              ><span
                class="rounded-[2px] border border-done-low block-3 inline-3"
              ></span>{m.coverage_band_under({ value: "25%" })}</span
            >
            <span class="flex items-center gap-1"
              ><span
                class="rounded-[2px] border border-done-some block-3 inline-3"
              ></span>{m.coverage_band({ from: "25%", to: "49%" })}</span
            >
            <span class="flex items-center gap-1"
              ><span
                class="rounded-[2px] border border-done-half block-3 inline-3"
              ></span>{m.coverage_band({ from: "50%", to: "74%" })}</span
            >
            <span class="flex items-center gap-1"
              ><span
                class="rounded-[2px] border border-done-most block-3 inline-3"
              ></span>{m.coverage_band({ from: "75%", to: "99%" })}</span
            >
            <span class="flex items-center gap-1"
              ><span class="rounded-[2px] bg-(--yes) block-3 inline-3"
              ></span>{m.coverage_ok()}</span
            >
          </p>
        </div>
        <div
          role="group"
          aria-label={m.coverage_view()}
          class="flex flex-wrap items-center gap-2 text-sm"
        >
          <span class="text-muted-foreground">{m.coverage_view()}</span>
          <span class="flex rounded-sm border border-border p-0.5">
            {#each VIEWS as option (option)}
              <button
                type="button"
                aria-pressed={view === option}
                class={cn(
                  "rounded-[3px] px-2 block-7 focus-visible:outline-2 focus-visible:outline-ring",
                  {
                    "bg-foreground text-background": view === option,
                    "text-muted-foreground hover:text-foreground":
                      view !== option,
                  },
                )}
                onclick={() => {
                  view = option;
                  try {
                    localStorage.setItem("coverage-view", option);
                  } catch {}
                }}
              >
                {option === "percent"
                  ? m.coverage_view_percent()
                  : option === "left"
                    ? m.coverage_view_left()
                    : m.coverage_view_done()}
              </button>
            {/each}
          </span>
          <button
            type="button"
            aria-pressed={hideDone}
            class={cn(
              "rounded-sm border border-border px-2 block-8 focus-visible:outline-2 focus-visible:outline-ring",
              {
                "bg-foreground text-background": hideDone,
                "text-muted-foreground hover:text-foreground": !hideDone,
              },
            )}
            onclick={() => {
              hideDone = !hideDone;
              try {
                localStorage.setItem("coverage-hide-done", String(hideDone));
              } catch {}
            }}
          >
            {m.coverage_hide_done()}
          </button>
        </div>
      </div>
    </header>
    <div role="treegrid" aria-label={m.coverage_title()} class="grid gap-px">
      <div
        role="row"
        class={cn(
          "sticky inset-bs-0 z-10 grid grid-cols-[minmax(0,1fr)_5.5rem_repeat(4,4.75rem)] items-end gap-x-1 bg-card px-2 pbs-2 pbe-1.5 text-xs/tight font-bold max-sm:pbs-16",
          {
            "max-sm:grid-cols-[minmax(0,1fr)_repeat(4,2.25rem)]":
              view !== "done",
            "max-sm:grid-cols-[minmax(0,1fr)_repeat(4,3rem)]": view === "done",
          },
        )}
      >
        <span role="columnheader">{m.coverage_column_name()}</span>
        <span role="columnheader" class="text-end max-sm:hidden"
          >{m.coverage_column_held()}</span
        >
        {#each [m.coverage_column_all(), m.coverage_column_county(), m.coverage_column_precinct(), m.coverage_column_shapes()] as heading (heading)}
          <span
            role="columnheader"
            class="text-center max-sm:origin-bottom-left max-sm:translate-x-3 max-sm:-rotate-45 max-sm:text-start max-sm:whitespace-nowrap"
            >{heading}</span
          >
        {/each}
      </div>
      <Rows rows={[summary, ...data.rows]} {view} {hideDone} />
    </div>
  </article>
</main>
