<script lang="ts">
  import { READ_ONLY } from "$app/env/public";
  import Term from "#lib/components/term.svelte";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { PageServerData } from "./$types";
  import Preview from "./preview.svelte";

  const {
    head,
    incumbency = null,
  }: {
    head: Extract<PageServerData, { kind: "contest" }>["head"];
    incumbency?: "defeated" | "open" | null;
  } = $props();

  const drilled = $derived(head.scope.noun != null);
  const heading = $derived(
    drilled && head.scope.abroad != null
      ? m.scope_abroad_title({ place: head.scope.name })
      : head.scope.name,
  );
</script>

{#if drilled && head.path != null}
  <Preview
    class={cn(
      "justify-self-start text-[0.9375rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground",
      { "max-md:pe-32": !READ_ONLY },
    )}
    path={head.path}>{head.name}</Preview
  >
{/if}
<h1 class={cn("title", { "max-md:pe-32": !READ_ONLY })} data-scope-title>
  {(drilled ? heading : head.name).replaceAll("\u2014", "\u2060—")}
</h1>
<p
  class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem] text-muted-foreground"
>
  {#if drilled}
    <span class="first-letter:uppercase"
      >{head.scope.abroad == null
        ? m.scope_in({
            noun: head.scope.noun ?? "",
            parent: head.scope.parent ?? "",
          })
        : head.scope.abroad.electorate == null
          ? m.scope_abroad_plain({ noun: head.scope.noun ?? "" })
          : m.scope_abroad({
              noun: head.scope.noun ?? "",
              electorate: head.scope.abroad.electorate,
            })}</span
    >
  {:else if head.election.path == null}
    <span>{head.election.name}</span>
  {:else}
    <Preview
      class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
      path={head.election.path}>{head.election.name}</Preview
    >
  {/if}
  <time datetime={head.election.date}>{date(head.election.date)}</time>
  {#if head.scope.home != null}
    <Preview
      class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
      path={head.scope.home}>{m.scope_home({ place: head.scope.name })}</Preview
    >
  {/if}
  {#if drilled}
    {#each head.scope.memberships.filter((row) => row.isParent) as row (row.name)}
      {const label = $derived(
        (row.isPartial ? m.scope_member_partial : m.scope_member)({
          place: row.name,
        }),
      )}
      {#if row.path == null}
        <span>{label}</span>
      {:else}
        <Preview
          class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          path={row.path}>{label}</Preview
        >
      {/if}
    {/each}
  {/if}
  {#if incumbency != null}
    <span class="highlighter px-1 font-semibold text-foreground"
      ><Term
        label={incumbency === "defeated"
          ? m.incumbency_defeated()
          : m.incumbency_open()}
        definition={incumbency === "defeated"
          ? m.term_incumbency_defeated()
          : m.term_incumbency_open()}
      /></span
    >
  {/if}
  {#if head.election.status === "annulled"}
    <span class="highlighter px-1 font-semibold text-foreground"
      >{m.election_annulled()}</span
    >
  {/if}
</p>
