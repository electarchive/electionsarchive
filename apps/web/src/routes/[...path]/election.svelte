<script lang="ts">
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";
  import Preview from "./preview.svelte";

  const {
    election,
  }: { election: Extract<PageData, { kind: "election" }>["election"] } =
    $props();

  const STATUSES: Record<string, () => string> = {
    scheduled: m.election_scheduled,
    postponed: m.election_postponed,
    canceled: m.election_canceled,
    annulled: m.election_annulled,
  };
</script>

<header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
  <h1
    class="font-serif text-[clamp(1.75rem,3vw,2.5rem)] leading-[1.02] font-[640] tracking-[-0.01em] text-balance"
  >
    {election.name}
  </h1>
  <p
    class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem] text-muted-foreground"
  >
    <time datetime={election.date}>{date(election.date)}</time>
    {#if STATUSES[election.status] != null}
      <span class="highlighter px-1 font-semibold text-foreground"
        >{STATUSES[election.status]?.()}</span
      >
    {/if}
  </p>
</header>

<section class="col-span-12 bg-card px-3.5 py-3">
  <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.election_contests()}</h2>
  <ul class="grid text-sm">
    {#each election.contests as contest, index (index)}
      <li
        class="flex items-baseline justify-between gap-4 border-be border-border/60 py-1.5 last:border-be-0"
      >
        {#if contest.path == null}
          <span class="font-semibold">{contest.name}</span>
        {:else}
          <Preview class="font-semibold hover:highlighter" path={contest.path}
            >{contest.name}</Preview
          >
        {/if}
        {#if contest.children > 0}
          <span class="shrink-0 text-[0.8125rem] text-muted-foreground"
            >{m.election_parts({ count: contest.children })}</span
          >
        {/if}
      </li>
    {/each}
  </ul>
</section>

{#if election.elections.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">
      {m.election_children()}
    </h2>
    <ul class="grid text-sm">
      {#each election.elections as child, index (index)}
        <li
          class="flex items-baseline justify-between gap-4 border-be border-border/60 py-1.5 last:border-be-0"
        >
          {#if child.path == null}
            <span>{child.name}</span>
          {:else}
            <Preview class="hover:highlighter" path={child.path}
              >{child.name}</Preview
            >
          {/if}
          <time
            class="shrink-0 font-mono text-xs text-muted-foreground"
            datetime={child.date}>{date(child.date)}</time
          >
        </li>
      {/each}
    </ul>
  </section>
{/if}

{#if election.notes.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.notes_title()}</h2>
    <ul class="grid gap-1.5 text-sm/snug text-muted-foreground">
      {#each election.notes as note, index (index)}
        <li class="max-inline-[72ch]">{note.body}</li>
      {/each}
    </ul>
  </section>
{/if}
