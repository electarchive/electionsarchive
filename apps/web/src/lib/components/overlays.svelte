<script lang="ts">
  import ConfirmationDialog from "#lib/components/confirmation-dialog.svelte";
  import Explain from "#lib/components/explain.svelte";
  import Provenance from "#lib/components/provenance.svelte";
  import { Toaster } from "#lib/components/ui/sonner/index.js";

  $effect(() => {
    const observer = new MutationObserver(() => {
      document.documentElement.toggleAttribute(
        "data-locked",
        document.body.style.overflow === "hidden",
      );
    });
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["style"],
    });
    return () => observer.disconnect();
  });
</script>

<Toaster />
<ConfirmationDialog />
<Provenance />
<Explain />
