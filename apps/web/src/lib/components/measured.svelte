<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "#lib/utils.js";

  let {
    width = $bindable(0),
    reserve = null,
    class: className,
    children,
  }: {
    width?: number;
    reserve?: string | null;
    class?: string;
    children: Snippet;
  } = $props();
</script>

<div
  bind:clientWidth={width}
  class={cn(className, { "island-skeleton": width === 0 && reserve != null })}
  style:block-size={width === 0 ? reserve : null}
>
  {#if width > 0}
    {@render children()}
  {:else if reserve != null}
    <span class="island-media block-full inline-full" aria-hidden="true"></span>
  {/if}
</div>
