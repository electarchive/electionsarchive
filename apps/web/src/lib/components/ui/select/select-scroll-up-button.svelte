<script lang="ts">
  import ChevronUpIcon from "@lucide/svelte/icons/chevron-up";
  import { Select as SelectPrimitive } from "bits-ui";
  import type { WithoutChildrenOrChild } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";

  let {
    ref = $bindable(null),
    class: className,
    ...restProps
  }: WithoutChildrenOrChild<SelectPrimitive.ScrollUpButtonProps> = $props();
</script>

<SelectPrimitive.ScrollUpButton
  bind:ref
  data-slot="select-scroll-up-button"
  class={cn(
    "z-10 flex cursor-default items-center justify-center bg-popover py-1 text-muted-foreground [&_svg:not([class*='size-'])]:size-3.5",
    className,
  )}
  {...restProps}
>
  <ChevronUpIcon />
</SelectPrimitive.ScrollUpButton>
