<script lang="ts">
  import type { Swing } from "#lib/live/flips.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    swing,
    party,
    isSmall = false,
  }: {
    swing: Swing;
    party: (id: string) => { short: string; color: string };
    isSmall?: boolean;
  } = $props();
</script>

<span class="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
  {#if isSmall}
    <span class="text-xs font-semibold"
      >{m.night_flips_count({
        flipped: swing.flipped,
        leading: swing.leading,
      })}</span
    >
  {:else}
    <span class="font-figures text-2xl font-[760] tabular-nums"
      >{swing.flipped}</span
    >
  {/if}
  {#each swing.parties as row (row.id)}
    {const found = $derived(party(row.id))}
    <span
      class={cn("inline-flex items-baseline gap-1 font-figures tabular-nums", {
        "text-sm font-[760]": !isSmall,
        "text-xs font-bold": isSmall,
      })}
      title={m.night_flips_party_about({
        party: found.short,
        flipped: row.flipped,
        leading: row.leading,
      })}
    >
      <span
        class="self-center rounded-[1px] block-2 inline-2"
        style:background-color={found.color}
      ></span>
      <span
        style:color="color-mix(in oklab, {found.color} 80%, var(--foreground))"
        >{found.short}{#if row.flipped > 0}&nbsp;+{row.flipped}{/if}</span
      >
      {#if row.leading > 0}
        <span class="text-xs font-semibold text-muted-foreground"
          >{m.night_flips_party_leading({ count: row.leading })}</span
        >
      {/if}
    </span>
  {/each}
</span>
