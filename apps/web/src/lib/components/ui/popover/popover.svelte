<script lang="ts">
  import { Popover as PopoverPrimitive } from "bits-ui";
  import { closeOnLeave } from "#lib/components/ui/leave.js";

  let { open = $bindable(false), ...restProps }: PopoverPrimitive.RootProps =
    $props();

  closeOnLeave(
    () => open,
    () => (open = false),
  );
</script>

<PopoverPrimitive.Root bind:open {...restProps} />
