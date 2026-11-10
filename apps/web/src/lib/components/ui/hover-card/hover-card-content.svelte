<script lang="ts">
  import type { ComponentProps } from "svelte";
  import type HoverCardBody from "./hover-card-body.svelte";

  let { ...restProps }: ComponentProps<typeof HoverCardBody> = $props();
</script>

{#await import("./hover-card-body.svelte") then { default: Body }}
  <Body {...restProps} />
{/await}
