<script lang="ts">
  import * as AlertDialog from "#lib/components/ui/alert-dialog/index.js";
  import { getConfirmationContext } from "#lib/stores/confirmation.svelte.js";

  const confirmationStore = getConfirmationContext();
</script>

<AlertDialog.Root
  open={confirmationStore.isOpen}
  onOpenChange={(open) => {
    if (!open) {
      confirmationStore.handleCancel();
    }
  }}
>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>{confirmationStore.options?.title}</AlertDialog.Title>
      <AlertDialog.Description>
        {confirmationStore.options?.description}
      </AlertDialog.Description>
    </AlertDialog.Header>
    <AlertDialog.Footer>
      <AlertDialog.Cancel onclick={() => confirmationStore.handleCancel()}>
        {confirmationStore.options?.cancelText}
      </AlertDialog.Cancel>
      <AlertDialog.Action
        variant={confirmationStore.options?.variant}
        onclick={() => confirmationStore.handleConfirm()}
      >
        {confirmationStore.options?.confirmText}
      </AlertDialog.Action>
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>
