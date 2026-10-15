<script lang="ts">
  import type { HTMLAttributes } from "svelte/elements";
  import type { WithElementRef } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";

  let {
    ref = $bindable(null),
    class: className,
    children,
    ...restProps
  }: WithElementRef<HTMLAttributes<HTMLSpanElement>> = $props();
</script>

<kbd
  bind:this={ref}
  data-slot="command-shortcut"
  class={cn("ms-auto", className)}
  {...restProps}
>
  {@render children?.()}
</kbd>
