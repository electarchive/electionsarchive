<script lang="ts">
  import { page } from "$app/state";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import type { PageData } from "./$types";
  import Rows from "./rows.svelte";

  const { data }: { data: PageData } = $props();

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
      <p class="text-xs text-muted-foreground">{built}</p>
    </header>
    <div role="treegrid" aria-label={m.coverage_title()} class="grid gap-px">
      <div
        role="row"
        class="sticky inset-bs-0 z-10 grid grid-cols-[minmax(0,1fr)_5.5rem_repeat(4,4.75rem)] items-end gap-x-1 bg-card px-2  pbs-2 pbe-1.5 text-xs/tight  font-bold max-sm:grid-cols-[minmax(0,1fr)_repeat(4,2.25rem)]"
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
      <Rows rows={data.rows} />
    </div>
  </article>
</main>
