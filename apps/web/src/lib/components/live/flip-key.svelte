<script lang="ts">
  import { hatch } from "#lib/live/flips.js";
  import { m } from "#lib/paraglide/messages.js";

  const { about = null }: { about?: string | null } = $props();

  const flipped = hatch("var(--muted-foreground)", "flipped");
  const leading = hatch("var(--muted-foreground)", "leading");
</script>

{#snippet swatch(mark: { fill: string; stripes: string }, label: string)}
  <span class="flex items-center gap-1.5">
    <span
      class="shrink-0 rounded-[1px] block-3.5 inline-3.5"
      style:background-color={mark.fill}
      style:background-image={mark.stripes}
      aria-hidden="true"
    ></span>
    {label}
  </span>
{/snippet}

<div class="grid gap-1 text-xs/4 text-muted-foreground">
  <span class="flex flex-wrap items-center gap-x-3 gap-y-1">
    {@render swatch(flipped, m.night_flip_status_flipped())}
    {@render swatch(leading, m.night_flip_status_leading())}
  </span>
  {#if about != null}
    <p class="text-pretty">{about}</p>
  {/if}
</div>
