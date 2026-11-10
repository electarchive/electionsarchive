<script lang="ts">
  import type { ComponentProps } from "svelte";
  import type TooltipBody from "./tooltip-body.svelte";

  let { ...restProps }: ComponentProps<typeof TooltipBody> = $props();
</script>

{#await import("./tooltip-body.svelte") then { default: Body }}
  <Body {...restProps} />
{/await}
