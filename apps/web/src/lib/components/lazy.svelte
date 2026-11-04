<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Part, Strategy } from "svelte-islands";
  import { Island } from "svelte-islands";

  const {
    part,
    view,
    when = "visible",
    ssr = true,
    defer = false,
    class: className,
    children,
  }: {
    part: Part | null;
    view: string;
    when?: Strategy;
    ssr?: boolean;
    defer?: boolean;
    class?: string;
    children: Snippet;
  } = $props();
</script>

{#if part == null}
  {@render children()}
{:else}
  <Island {part} {view} {when} {ssr} {defer} class={className}>
    {@render children()}
  </Island>
{/if}
