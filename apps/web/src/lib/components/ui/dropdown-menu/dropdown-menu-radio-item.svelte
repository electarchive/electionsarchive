<script lang="ts">
  import CheckIcon from "@lucide/svelte/icons/check";
  import { DropdownMenu as DropdownMenuPrimitive } from "bits-ui";
  import type { WithoutChild } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";

  let {
    ref = $bindable(null),
    class: className,
    children: childrenProp,
    closeOnSelect = false,
    ...restProps
  }: WithoutChild<DropdownMenuPrimitive.RadioItemProps> = $props();
</script>

<DropdownMenuPrimitive.RadioItem
  bind:ref
  {closeOnSelect}
  data-slot="dropdown-menu-radio-item"
  class={cn("menu-item pe-8 data-inset:ps-8", className)}
  {...restProps}
>
  {#snippet children({ checked })}
    <span
      class="pointer-events-none absolute end-2 flex items-center justify-center [&_svg]:size-3.5 [&_svg]:stroke-[2.5]"
      data-slot="dropdown-menu-radio-item-indicator"
    >
      {#if checked}
        <CheckIcon />
      {/if}
    </span>
    {@render childrenProp?.({ checked })}
  {/snippet}
</DropdownMenuPrimitive.RadioItem>
