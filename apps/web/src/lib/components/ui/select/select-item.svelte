<script lang="ts">
  import CheckIcon from "@lucide/svelte/icons/check";
  import { Select as SelectPrimitive } from "bits-ui";
  import type { WithoutChild } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";

  let {
    ref = $bindable(null),
    class: className,
    value,
    label,
    children: childrenProp,
    ...restProps
  }: WithoutChild<SelectPrimitive.ItemProps> = $props();
</script>

<SelectPrimitive.Item
  bind:ref
  {value}
  {label}
  data-slot="select-item"
  class={cn(
    "menu-item w-full pe-8 *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2",
    className,
  )}
  {...restProps}
>
  {#snippet children({ selected, highlighted })}
    <span
      class="pointer-events-none absolute end-2 flex size-4 items-center justify-center"
    >
      {#if selected}
        <CheckIcon class="pointer-events-none size-3.5 stroke-[2.5]" />
      {/if}
    </span>
    <span class="flex flex-1 shrink-0 gap-2 whitespace-nowrap">
      {#if childrenProp}
        {@render childrenProp({ selected, highlighted })}
      {:else}
        {label || value}
      {/if}
    </span>
  {/snippet}
</SelectPrimitive.Item>
