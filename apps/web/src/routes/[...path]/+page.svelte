<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";
  import Contest from "./contest.svelte";
  import Election from "./election.svelte";
  import Polity from "./polity.svelte";
  import Preview from "./preview.svelte";

  const { data }: { data: PageData } = $props();

  const crumbs = $derived(
    data.kind === "contest"
      ? [
          ...data.contest.breadcrumbs,
          {
            name: data.contest.election.name,
            path: data.contest.election.path,
          },
        ]
      : data.kind === "election"
        ? [
            ...data.election.breadcrumbs,
            ...(data.election.parent == null ? [] : [data.election.parent]),
          ]
        : data.polity.breadcrumbs,
  );
  const title = $derived(
    data.kind === "contest"
      ? data.contest.scope.noun == null
        ? data.contest.name.includes(data.contest.election.name)
          ? data.contest.name
          : `${data.contest.name}, ${data.contest.election.name}`
        : `${data.contest.scope.name}, ${data.contest.name}`
      : data.kind === "election"
        ? data.election.name
        : data.polity.name,
  );
</script>

<svelte:head>
  <title>{title} | {m.wordmark()}</title>
</svelte:head>

<main class="p-3.5 max-sm:p-0">
  <article
    class="grid grid-cols-12 gap-px overflow-hidden rounded-lg border bg-border max-sm:rounded-none max-sm:border-x-0"
  >
    {#if crumbs.length > 0}
      <nav
        class="col-span-12 flex flex-wrap items-center gap-x-2 gap-y-1 bg-card px-3.5 py-2.5 text-sm text-muted-foreground"
        aria-label={m.breadcrumbs()}
      >
        {#each crumbs as crumb, index (index)}
          {#if index > 0}
            <span aria-hidden="true">/</span>
          {/if}
          {#if crumb.path == null}
            <span>{crumb.name}</span>
          {:else}
            <Preview
              class="hover:highlighter hover:text-foreground"
              path={crumb.path}>{crumb.name}</Preview
            >
          {/if}
        {/each}
      </nav>
    {/if}
    {#if data.kind === "contest"}
      <Contest contest={data.contest} lists={data.lists} match={data.match} />
    {:else if data.kind === "election"}
      <Election election={data.election} />
    {:else}
      <Polity polity={data.polity} />
    {/if}
  </article>
</main>
