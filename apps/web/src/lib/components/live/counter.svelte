<script lang="ts">
  import { cubicOut } from "svelte/easing";
  import { prefersReducedMotion, Tween } from "svelte/motion";
  import { cn } from "#lib/utils.js";

  const {
    value,
    format,
    class: className = "",
  }: {
    value: number;
    format: (value: number) => string;
    class?: string;
  } = $props();

  const tween = Tween.of(() => value, {
    duration: () => (prefersReducedMotion.current ? 0 : 900),
    easing: cubicOut,
  });
</script>

<span class={cn("font-figures tabular-nums", className)}
  >{format(tween.current)}</span
>
