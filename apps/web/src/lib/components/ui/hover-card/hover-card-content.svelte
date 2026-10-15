<script lang="ts">
  import type { ComponentProps } from "svelte";
  import { LinkPreview as HoverCardPrimitive } from "bits-ui";
  import type { WithoutChildrenOrChild } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";
  import HoverCardPortal from "./hover-card-portal.svelte";

  let {
    ref = $bindable(null),
    class: className,
    side = "bottom",
    sideOffset = 4,
    align = "center",
    portalProps,
    ...restProps
  }: HoverCardPrimitive.ContentProps & {
    portalProps?: WithoutChildrenOrChild<
      ComponentProps<typeof HoverCardPortal>
    >;
  } = $props();
</script>

<HoverCardPortal {...portalProps}>
  <HoverCardPrimitive.Content
    bind:ref
    data-slot="hover-card-content"
    {side}
    {sideOffset}
    {align}
    class={cn(
      "menu w-64 origin-(--bits-link-preview-content-transform-origin) p-3 text-sm",
      className,
    )}
    {...restProps}
  />
</HoverCardPortal>
