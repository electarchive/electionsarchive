<script lang="ts">
  import type { Bounty } from "#lib/server/bounties.js";
  import Moment from "#lib/components/moment.svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    bounty,
  }: {
    bounty: Pick<
      Bounty,
      | "status"
      | "claimer"
      | "claimer_handle"
      | "claimed_until"
      | "proposal"
      | "filler"
      | "filler_handle"
      | "closed_at"
    >;
  } = $props();

  const NAME = "\u0001name\u0001";
  const PROPOSAL = "\u0001proposal\u0001";
  const DATE = "\u0001date\u0001";

  const pieces = $derived.by(() => {
    if (bounty.status === "fulfilled" && bounty.proposal != null) {
      return {
        text: m.bounty_filled({ name: NAME, proposal: PROPOSAL, date: DATE }),
        at: bounty.closed_at,
        name: bounty.filler ?? m.feed_someone(),
        handle: bounty.filler_handle,
      };
    }
    if (bounty.status === "closed") {
      return {
        text: m.bounty_closed({ date: DATE }),
        at: bounty.closed_at,
        name: null,
        handle: null,
      };
    }
    if (bounty.claimer != null) {
      return {
        text: m.bounty_claimed({ name: NAME, date: DATE }),
        at: bounty.claimed_until,
        name: bounty.claimer,
        handle: bounty.claimer_handle,
      };
    }
    return { text: m.bounty_unclaimed(), at: null, name: null, handle: null };
  });
  const muted = $derived(bounty.status === "closed" || pieces.name == null);
</script>

<p class={cn("text-sm text-pretty", { "text-muted-foreground": muted })}>
  {#each pieces.text.split("\u0001") as piece, index (index)}
    {#if index % 2 === 1 && piece === "name"}
      {#if pieces.handle == null}
        {pieces.name}
      {:else}
        <a
          class="underline underline-offset-2"
          href="/contributors/{pieces.handle}">{pieces.name}</a
        >
      {/if}
    {:else if index % 2 === 1 && piece === "date" && pieces.at != null}
      <Moment at={pieces.at} style="date" />
    {:else if index % 2 === 1 && piece === "proposal"}
      <a
        class="underline underline-offset-2"
        href="/proposals/{bounty.proposal}">#{bounty.proposal}</a
      >
    {:else}
      {piece}
    {/if}
  {/each}
</p>
