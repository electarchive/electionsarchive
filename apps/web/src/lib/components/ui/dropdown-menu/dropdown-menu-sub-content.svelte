<script lang="ts">
  import type { ComponentProps } from "svelte";
  import { DropdownMenu as DropdownMenuPrimitive } from "bits-ui";
  import type { WithoutChildrenOrChild } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";
  import DropdownMenuPortal from "./dropdown-menu-portal.svelte";

  let {
    ref = $bindable(null),
    class: className,
    align = "start",
    alignOffset = -3,
    portalProps,
    ...restProps
  }: DropdownMenuPrimitive.SubContentProps & {
    portalProps?: WithoutChildrenOrChild<
      ComponentProps<typeof DropdownMenuPortal>
    >;
  } = $props();
</script>

<DropdownMenuPortal {...portalProps}>
  <DropdownMenuPrimitive.SubContent
    bind:ref
    data-slot="dropdown-menu-sub-content"
    {align}
    {alignOffset}
    class={cn(
      "menu min-w-36 origin-(--bits-dropdown-menu-content-transform-origin) overflow-hidden p-1",
      className,
    )}
    {...restProps}
  />
</DropdownMenuPortal>
