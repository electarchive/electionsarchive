<script lang="ts">
  const { size = 24, class: className }: { size?: number; class?: string } =
    $props();
</script>

<svg
  class={className}
  width={size}
  height={size}
  viewBox="0 0 32 32"
  aria-hidden="true"
>
  <rect class="fill-foreground" width="32" height="32" rx="3" />
  <path
    class="fill-none stroke-background"
    d="M9.5 7 V25 M13.75 7 V25 M18 7 V25 M22.25 7 V25 M5.5 21 L26.5 11"
    stroke-width="2.4"
    stroke-linecap="square"
  />
</svg>
