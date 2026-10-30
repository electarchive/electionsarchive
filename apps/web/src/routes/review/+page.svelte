<script lang="ts">
  import { resolve } from "$app/paths";
  import { stateLabel, tierLabel } from "#lib/contrib.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();
</script>

<svelte:head>
  <title>{m.review_title()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<div class="mx-auto px-4 pbs-6 pbe-24 max-inline-5xl max-sm:px-3">
  <h1 class="title">{m.review_title()}</h1>
  {#if !data.allowed}
    <p class="mbs-4 text-sm text-muted-foreground">{m.review_forbidden()}</p>
  {:else if data.proposals.length === 0}
    <p class="mbs-4 text-sm text-muted-foreground">{m.review_empty()}</p>
  {:else}
    <div class="mbs-5 overflow-x-auto">
      <table class="border-bs text-sm inline-full">
        <thead>
          <tr class="border-be text-start text-muted-foreground">
            <th class="py-2 pe-4 text-start font-normal"
              >{m.review_col_proposal()}</th
            >
            <th class="py-2 pe-4 text-start font-normal"
              >{m.review_col_author()}</th
            >
            <th class="py-2 pe-4 text-start font-normal"
              >{m.review_col_tier()}</th
            >
            <th class="py-2 pe-4 text-end font-normal"
              >{m.review_col_changes()}</th
            >
            <th class="py-2 pe-4 text-end font-normal"
              >{m.review_col_findings()}</th
            >
            <th class="py-2 text-end font-normal">{m.review_col_submitted()}</th
            >
          </tr>
        </thead>
        <tbody>
          {#each data.proposals as item (item.number)}
            <tr class="border-be align-baseline">
              <td class="py-2 pe-4">
                <a
                  class="grid hover:underline"
                  href={resolve("/proposals/[number]", {
                    number: String(item.number),
                  })}
                >
                  <span
                    ><span class="font-figures text-muted-foreground"
                      >#{item.number}</span
                    >
                    <span class="font-semibold">{item.title}</span></span
                  >
                  {#if item.state !== "open"}
                    <span class="text-xs text-muted-foreground"
                      >{stateLabel[item.state]()}</span
                    >
                  {/if}
                </a>
              </td>
              <td class="py-2 pe-4"
                >{item.author ?? m.proposal_unknown_author()}</td
              >
              <td class="py-2 pe-4 text-muted-foreground"
                >{item.tier == null ? "" : tierLabel[item.tier]?.()}</td
              >
              <td class="py-2 pe-4 text-end font-figures"
                >{count(item.changes)}</td
              >
              <td class="py-2 pe-4 text-end font-figures"
                >{count(item.findings)}</td
              >
              <td class="py-2 text-end font-figures whitespace-nowrap"
                >{date(item.submitted_at?.slice(0, 10) ?? null)}</td
              >
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
