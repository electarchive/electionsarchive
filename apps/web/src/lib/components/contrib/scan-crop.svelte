<script lang="ts">
  import { cn } from "#lib/utils.js";

  const {
    href,
    width,
    height,
    region,
    label,
    limit = 320,
    class: className,
  }: {
    href: string;
    width: number;
    height: number;
    region: readonly [number, number, number, number] | null;
    label: string;
    limit?: number;
    class?: string;
  } = $props();

  const box = $derived(region ?? ([0, 0, 1, 1] as const));
  const left = $derived(Math.max(0, box[0] - 0.12) * width);
  const top = $derived(Math.max(0, box[1] - 0.04) * height);
  const across = $derived(Math.min(1, box[2] + 0.12) * width - left);
  const down = $derived(Math.min(1, box[3] + 0.04) * height - top);
</script>

<svg
  class={cn(
    "rounded-sm bg-white ring-1 ring-border block-auto max-inline-full",
    className,
  )}
  width={region == null ? limit : across}
  viewBox="{left} {top} {across} {down}"
  role="img"
  aria-label={label}
>
  <image {href} {width} {height} />
  {#if region != null}
    <rect
      x={box[0] * width - 3}
      y={box[1] * height - 2}
      width={(box[2] - box[0]) * width + 6}
      height={(box[3] - box[1]) * height + 4}
      rx="2"
      class="fill-marker/55 stroke-ring mix-blend-multiply"
      stroke-width="1.5"
    />
  {/if}
</svg>
