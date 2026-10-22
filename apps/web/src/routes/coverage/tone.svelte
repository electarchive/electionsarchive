<script lang="ts">
  import { cn } from "#lib/utils.js";
  import type { Reason } from "./reasons.js";

  const {
    reason,
    class: className,
    style,
  }: { reason: Reason; class?: string; style?: string } = $props();
</script>

<span
  aria-hidden="true"
  {style}
  class={cn(
    className,
    {
      "bg-(--yes)": reason === "complete",
      "bg-(--done-most)": reason === "coarse",
      "bg-(--done-half)": reason === "partial",
      "bg-(--done-some)": reason === "totals",
      "bg-muted-foreground/45": reason === "bare",
    },
    { "border border-muted-foreground/60 bg-muted": reason === "absent" },
  )}
></span>
