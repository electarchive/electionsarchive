<script lang="ts">
  import { cubicOut } from "svelte/easing";
  import { prefersReducedMotion, Tween } from "svelte/motion";

  const {
    value,
    format,
  }: { value: number; format: (value: number) => string } = $props();

  const tween = Tween.of(() => value, {
    duration: () => (prefersReducedMotion.current ? 0 : 640),
    easing: cubicOut,
  });
</script>

{format(tween.current)}
