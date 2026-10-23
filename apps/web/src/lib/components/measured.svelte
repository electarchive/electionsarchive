<script lang="ts">
  import type { Snippet } from "svelte";

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
  class={className}
  style:block-size={width === 0 ? reserve : null}
>
  {#if width > 0}
    {@render children()}
  {/if}
</div>
