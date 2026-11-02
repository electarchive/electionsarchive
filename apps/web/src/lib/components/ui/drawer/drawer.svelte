<script lang="ts">
  import { Drawer as DrawerPrimitive } from "vaul-svelte";
  import { closeOnLeave } from "#lib/components/ui/leave.js";

  let {
    shouldScaleBackground = true,
    open = $bindable(false),
    activeSnapPoint = $bindable(null),
    ...restProps
  }: DrawerPrimitive.RootProps = $props();

  closeOnLeave(
    () => open,
    () => (open = false),
  );
</script>

<DrawerPrimitive.Root
  {shouldScaleBackground}
  bind:open
  bind:activeSnapPoint
  {...restProps}
/>
