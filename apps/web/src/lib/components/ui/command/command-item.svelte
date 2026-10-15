<script lang="ts">
  import CheckIcon from "@lucide/svelte/icons/check";
  import { Command as CommandPrimitive } from "bits-ui";
  import { cn } from "#lib/utils.js";

  let {
    ref = $bindable(null),
    class: className,
    children,
    ...restProps
  }: CommandPrimitive.ItemProps = $props();
</script>

<CommandPrimitive.Item
  bind:ref
  data-slot="command-item"
  class={cn(
    "group/command-item menu-item data-selected:highlighter",
    className,
  )}
  {...restProps}
>
  {@render children?.()}
  <CheckIcon
    class="ms-auto size-3.5 stroke-[2.5] opacity-0 group-has-data-[slot=command-shortcut]/command-item:hidden group-data-[checked=true]/command-item:opacity-100"
  />
</CommandPrimitive.Item>
