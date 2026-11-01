<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";

  const { party, color }: { party: string; color: string } = $props();
</script>

<span
  class="inline-flex shrink-0 items-center rounded-xs px-1 text-[11px]/4 font-semibold whitespace-nowrap"
  style:color="color-mix(in oklab, {color} 72%, var(--foreground))"
  style:background-color="color-mix(in oklab, {color} 16%, var(--background))"
  style:box-shadow="inset 0 0 0 1px color-mix(in oklab, {color} 45%, var(--background))"
  title={m.night_explorer_flip({ party })}
  ><span aria-hidden="true">{m.night_flip()}</span><span class="sr-only"
    >{m.night_explorer_flip({ party })}</span
  ></span
>
