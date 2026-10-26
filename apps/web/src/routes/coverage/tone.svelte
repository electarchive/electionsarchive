<script lang="ts">
  import { cn } from "#lib/utils.js";
  import type { Reason } from "#lib/coverage.js";

  const {
    reason,
    class: className,
    style,
  }: {
    reason: Reason | "icpsr" | "published";
    class?: string;
    style?: string;
  } = $props();
</script>

<span
  aria-hidden="true"
  {style}
  class={cn(
    className,
    {
      "bg-(--yes)":
        reason === "complete" || reason === "icpsr" || reason === "published",
      "bg-(--done-most)":
        reason === "coarse" || reason === "units" || reason === "major",
      "bg-(--done-half)": reason === "partial" || reason === "unshaped",
      "bg-(--done-some)": reason === "totals",
      "bg-muted-foreground/45": reason === "bare" || reason === "undrawn",
    },
    {
      "border border-muted-foreground/70 bg-muted bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1.5px,transparent_1.5px_5px)]":
        reason === "absent",
    },
    {
      "bg-[repeating-linear-gradient(135deg,var(--card)_0_1.5px,transparent_1.5px_4.5px)]":
        reason === "icpsr" || reason === "major",
    },
    {
      "bg-[repeating-linear-gradient(0deg,var(--card)_0_1px,transparent_1px_4px)]":
        reason === "published",
    },
  )}
></span>
