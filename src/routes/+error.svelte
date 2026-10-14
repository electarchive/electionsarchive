<script lang="ts">
  import { page } from "$app/state";
  import { CircleAlertIcon } from "@lucide/svelte";

  const ERROR_CODE_LABELS: Record<number, string> = {
    404: "page not found",
    500: "server error",
    429: "too many requests",
  };
</script>

<div class="grid justify-items-center gap-2 py-20 text-center sm:py-40">
  <CircleAlertIcon class="text-destructive block-10 inline-10" />
  <h1 class="text-3xl font-bold">{page.status}</h1>
  <div class="text-muted-foreground">
    {ERROR_CODE_LABELS[page.status] ?? "an error occurred"}
  </div>
</div>
