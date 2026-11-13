<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import { ACCESS_LABELS } from "#lib/coverage.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";

  const {
    polity,
    place,
    level,
    year,
  }: { polity: string; place: string; level: string; year: number } = $props();

  const fetched = createQuery(() =>
    orpc.coverage.governments.queryOptions({
      input: { polity, state: place, level, year },
    }),
  );
  let isAll = $state(false);
  const rows = $derived(fetched.data?.rows ?? []);
  const list = $derived(isAll ? rows : rows.slice(0, 10));
  const host = (url: string) => (URL.canParse(url) ? new URL(url).host : url);
</script>

{#if fetched.data == null}
  <div role="row" class="bg-card px-3.5 py-1.5 text-sm text-muted-foreground">
    <span role="gridcell" class="ps-15">{m.coverage_loading()}</span>
  </div>
{:else if rows.length === 0}
  <div role="row" class="bg-card px-3.5 py-1.5 text-sm text-muted-foreground">
    <span role="gridcell" class="ps-15">{m.coverage_empty()}</span>
  </div>
{/if}
{#each list as row (row.id)}
  <div role="row" aria-level={4} class="bg-card px-3.5 py-1.5 text-sm">
    <div role="gridcell" class="grid gap-0.5 ps-15">
      <span class="flex flex-wrap items-baseline gap-x-2">
        {#if row.path == null}
          <span class="font-semibold">{row.name}</span>
        {:else}
          <a
            class="font-semibold underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            href={resolve("/[...path]", { path: row.path })}>{row.name}</a
          >
        {/if}
        {#if row.population != null}
          <span class="text-xs text-muted-foreground tabular-nums"
            >{count(row.population)}</span
          >
        {/if}
        {#if row.missing === true}
          <span class="text-xs font-semibold text-destructive"
            >{m.coverage_gov_missing()}</span
          >
        {/if}
      </span>
      <span
        class="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground tabular-nums"
      >
        <span
          >{row.last == null
            ? m.coverage_gov_none()
            : m.coverage_gov_last({ year: String(row.last) })}</span
        >
        {#if row.next != null}
          {#if row.next.path == null}
            <span>{m.coverage_gov_next({ date: date(row.next.date) })}</span>
          {:else}
            <a
              class="underline underline-offset-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
              href={resolve("/[...path]", { path: row.next.path })}
              >{m.coverage_gov_next({ date: date(row.next.date) })}</a
            >
          {/if}
        {/if}
        {#if row.cadence != null}
          <span
            >{m.coverage_gov_cycle()}
            {#if row.cadence.url == null}
              {row.cadence.title}
            {:else}
              <a
                class="underline underline-offset-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                href={row.cadence.url}>{row.cadence.title}</a
              >
            {/if}{#if row.cadence.locator != null}, {row.cadence
                .locator}{/if}</span
          >
        {/if}
        {#if row.access != null}
          <span
            >{m.coverage_gov_results()}
            {#if row.access.url == null}
              {row.access.platform}
            {:else}
              <a
                class="underline underline-offset-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                href={row.access.url}>{host(row.access.url)}</a
              >
            {/if}
            · {ACCESS_LABELS[row.access.access]()}</span
          >
        {/if}
      </span>
    </div>
  </div>
{/each}
{#if !isAll && rows.length > list.length}
  <div role="row" class="bg-card px-3.5 py-1.5 text-sm">
    <span role="gridcell" class="ps-15">
      <button
        type="button"
        class="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
        onclick={() => (isAll = true)}
        >{m.coverage_show_all({ count: count(rows.length) })}</button
      >
    </span>
  </div>
{:else if fetched.data != null && fetched.data.total > rows.length}
  <div role="row" class="bg-card px-3.5 py-1.5 text-sm text-muted-foreground">
    <span role="gridcell" class="ps-15"
      >{m.coverage_gov_more({
        count: count(fetched.data.total - rows.length),
      })}</span
    >
  </div>
{/if}
