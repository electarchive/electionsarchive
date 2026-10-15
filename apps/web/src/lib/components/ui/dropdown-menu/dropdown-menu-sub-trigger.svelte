<script lang="ts">
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { DropdownMenu as DropdownMenuPrimitive } from "bits-ui";
  import { cn } from "#lib/utils.js";

  let {
    ref = $bindable(null),
    class: className,
    inset,
    children,
    ...restProps
  }: DropdownMenuPrimitive.SubTriggerProps & { inset?: boolean } = $props();
</script>

<DropdownMenuPrimitive.SubTrigger
  bind:ref
  data-slot="dropdown-menu-sub-trigger"
  data-inset={inset}
  class={cn("menu-item data-inset:ps-8 data-open:highlighter", className)}
  {...restProps}
>
  {@render children?.()}
  <ChevronRightIcon
    class="ms-auto size-3.5 text-muted-foreground rtl:-scale-x-100"
  />
</DropdownMenuPrimitive.SubTrigger>
