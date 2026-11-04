<script lang="ts">
  import { STRIPE } from "#lib/live/flips.js";

  const {
    id,
    stripes,
    box,
    rect,
  }: {
    id: string;
    stripes: Array<string>;
    box: [number, number];
    rect: DOMRectReadOnly | undefined;
  } = $props();

  const unit = $derived(
    rect == null || rect.width === 0 || rect.height === 0
      ? box[0] / 150
      : Math.max(box[0] / rect.width, box[1] / rect.height),
  );
</script>

{#each stripes as stripe, index (stripe)}
  <pattern
    id="{id}-{index}"
    width={STRIPE.period * unit}
    height={STRIPE.period * unit}
    patternUnits="userSpaceOnUse"
    patternTransform="rotate(45)"
  >
    <rect
      width={STRIPE.width * unit}
      height={STRIPE.period * unit}
      fill={stripe}
    />
  </pattern>
{/each}
