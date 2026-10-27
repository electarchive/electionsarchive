<script lang="ts" generics="T = never">
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import { isTouch } from "#lib/explain.svelte.js";

  let {
    ref = $bindable(null),
    tether,
    onclick,
    ...restProps
  }: TooltipPrimitive.TriggerProps<T> = $props();
</script>

<TooltipPrimitive.Trigger
  bind:ref
  data-slot="tooltip-trigger"
  {tether}
  onclick={(event) => {
    onclick?.(event);
    const node = event.currentTarget;
    if (
      tether != null &&
      isTouch(event) &&
      !event.defaultPrevented &&
      node.closest("a[href]") == null
    ) {
      queueMicrotask(() => tether.open(node.id));
    }
  }}
  {...restProps}
/>
