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
      "bg-(--done-most)": reason === "coarse" || reason === "units",
      "bg-(--done-half)": reason === "partial" || reason === "unshaped",
      "bg-(--done-some)": reason === "totals",
      "bg-muted-foreground/45": reason === "bare" || reason === "undrawn",
    },
    {
      "border border-muted-foreground/70 bg-muted bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1.5px,transparent_1.5px_5px)]":
        reason === "absent",
    },
  )}
></span>
