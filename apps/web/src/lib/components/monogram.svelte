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
  <rect
    class="fill-card stroke-foreground"
    x="1"
    y="1"
    width="30"
    height="30"
    rx="4"
    stroke-width="1.5"
  />
  <path class="fill-primary" d="M5 12.6 L27 11.4 L27 19.4 L5 20.6 Z" />
  <path
    class="fill-none stroke-foreground"
    d="M9 8 V24 M13 8 V24 M17 8 V24 M21 8 V24 M6 21.5 L26 12"
    stroke-width="2.2"
    stroke-linecap="round"
  />
</svg>
