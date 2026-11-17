<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Native } from "#lib/native.js";
  import { ordered } from "#lib/native.js";

  const {
    name,
    native,
    format = (text: string) => text,
    children,
  }: {
    name: string;
    native: Native;
    format?: (text: string) => string;
    children?: Snippet;
  } = $props();

  const shown = $derived(ordered(name, native));
</script>

{#if shown.primary.lang == null}{format(shown.primary.text)}{:else}<span
    lang={shown.primary.lang}>{format(shown.primary.text)}</span
  >{/if}{@render children?.()}{#if shown.secondary != null}<span
    class="mbs-1.5 block font-sans text-[0.9375rem] leading-snug font-normal tracking-normal text-muted-foreground"
    lang={shown.secondary.lang}>{shown.secondary.text}</span
  >{/if}
