<script lang="ts">
  import { Drawer as DrawerPrimitive } from "vaul-svelte";
  import { cn } from "#lib/utils.js";

  let {
    ref = $bindable(null),
    class: className,
    ...restProps
  }: DrawerPrimitive.OverlayProps = $props();
</script>

<DrawerPrimitive.Overlay
  bind:ref
  data-slot="drawer-overlay"
  class={cn(
    "fixed inset-0 z-50 bg-[color-mix(in_oklab,var(--foreground)_24%,transparent)] duration-150 motion-reduce:animate-none data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0 data-closed:fill-mode-forwards",
    className,
  )}
  {...restProps}
/>
