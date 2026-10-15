<script lang="ts">
  import type { ComponentProps } from "svelte";
  import { AlertDialog as AlertDialogPrimitive } from "bits-ui";
  import type { WithoutChild, WithoutChildrenOrChild } from "#lib/utils.js";
  import { cn } from "#lib/utils.js";
  import AlertDialogOverlay from "./alert-dialog-overlay.svelte";
  import AlertDialogPortal from "./alert-dialog-portal.svelte";

  let {
    ref = $bindable(null),
    class: className,
    size = "default",
    portalProps,
    ...restProps
  }: WithoutChild<AlertDialogPrimitive.ContentProps> & {
    size?: "default" | "sm";
    portalProps?: WithoutChildrenOrChild<
      ComponentProps<typeof AlertDialogPortal>
    >;
  } = $props();
</script>

<AlertDialogPortal {...portalProps}>
  <AlertDialogOverlay />
  <AlertDialogPrimitive.Content
    bind:ref
    data-slot="alert-dialog-content"
    data-size={size}
    class={cn(
      "group/alert-dialog-content fixed top-1/2 left-1/2 menu z-50 grid max-h-[calc(100dvh-0.5rem)] w-full -translate-x-1/2 -translate-y-1/2 gap-5 overflow-y-auto rounded-lg p-5 data-[size=default]:max-w-xs data-[size=sm]:max-w-xs data-[size=default]:sm:max-w-lg",
      className,
    )}
    {...restProps}
  />
</AlertDialogPortal>
