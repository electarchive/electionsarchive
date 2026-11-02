<script lang="ts">
  import { Dialog as DialogPrimitive } from "bits-ui";
  import { closeOnLeave } from "#lib/components/ui/leave.js";

  let { open = $bindable(false), ...restProps }: DialogPrimitive.RootProps =
    $props();

  closeOnLeave(
    () => open,
    () => (open = false),
  );
</script>

<DialogPrimitive.Root bind:open {...restProps} />
