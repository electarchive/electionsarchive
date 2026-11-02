<script lang="ts">
  import { Drawer as DrawerPrimitive } from "vaul-svelte";
  import { closeOnLeave } from "#lib/components/ui/leave.js";

  let {
    shouldScaleBackground = true,
    open = $bindable(false),
    activeSnapPoint = $bindable(null),
    autoFocus,
    ...restProps
  }: DrawerPrimitive.RootProps = $props();

  let keyboard = $state(false);

  closeOnLeave(
    () => open,
    () => (open = false),
  );
</script>

<svelte:document
  onpointerdown={() => (keyboard = false)}
  onkeydown={() => (keyboard = true)}
/>

<DrawerPrimitive.Root
  {shouldScaleBackground}
  autoFocus={autoFocus ?? keyboard}
  bind:open
  bind:activeSnapPoint
  {...restProps}
/>
