<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLDetailsAttributes } from "svelte/elements";

  let {
    summary,
    children,
    open = $bindable(false),
    ...rest
  }: Omit<HTMLDetailsAttributes, "children" | "open"> & {
    summary: Snippet;
    children: Snippet;
    open?: boolean;
  } = $props();
</script>

<details {...rest} bind:open>
  {@render summary()}
  {#if open}
    {@render children()}
  {/if}
</details>
