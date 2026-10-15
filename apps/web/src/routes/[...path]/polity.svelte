<script lang="ts">
  import { resolve } from "$app/paths";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    polity,
  }: { polity: Extract<PageData, { kind: "polity" }>["polity"] } = $props();

  const STATUSES: Record<string, () => string> = {
    scheduled: m.election_scheduled,
    postponed: m.election_postponed,
    canceled: m.election_canceled,
    annulled: m.election_annulled,
  };
</script>

<header class="col-span-12 bg-card px-3.5 pbs-4 pbe-3.5">
  <h1
    class="font-serif text-[clamp(1.75rem,3vw,2.5rem)] leading-[1.02] font-[640] tracking-[-0.01em] text-balance"
  >
    {polity.name}
  </h1>
</header>

<section
  class={cn("col-span-12 bg-card px-3.5 py-3", {
    "lg:col-span-8": polity.polities.length > 0,
  })}
>
  <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.polity_elections()}</h2>
  {#if polity.elections.length === 0}
    <p class="text-sm text-muted-foreground">{m.polity_no_elections()}</p>
  {:else}
    <ul class="grid text-sm">
      {#each polity.elections as election, index (index)}
        <li
          class="grid grid-cols-[9.5rem_minmax(0,1fr)] items-baseline gap-3 border-be border-border/60 py-1.5 last:border-be-0 max-sm:grid-cols-1 max-sm:gap-0"
        >
          <time
            class="font-mono text-xs text-muted-foreground tabular-nums"
            datetime={election.date}>{date(election.date)}</time
          >
          <span class="flex flex-wrap items-baseline gap-x-2">
            {#if election.path == null}
              <span>{election.name}</span>
            {:else}
              <a
                class="hover:highlighter"
                href={resolve("/[...path]", { path: election.path })}
                >{election.name}</a
              >
            {/if}
            {#if STATUSES[election.status] != null}
              <span class="highlighter px-1 text-xs font-semibold"
                >{STATUSES[election.status]?.()}</span
              >
            {/if}
          </span>
        </li>
      {/each}
    </ul>
  {/if}
</section>

{#if polity.polities.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3 lg:col-span-4">
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">
      {m.polity_subdivisions()}
    </h2>
    <ul class="columns-2 gap-6 text-sm">
      {#each polity.polities as child, index (index)}
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
