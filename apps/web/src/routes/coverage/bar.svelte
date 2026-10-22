<script lang="ts">
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { Counts, Goal } from "./reasons.js";
  import { LABELS, REASONS } from "./reasons.js";
  import Tone from "./tone.svelte";

  const {
    counts,
    goal,
    class: className,
  }: { counts: Counts; goal: Goal; class?: string } = $props();

  const parts = $derived(
    REASONS[goal].filter((reason) => reason !== "absent" && counts[reason] > 0),
  );
  const title = $derived(
    REASONS[goal]
      .filter((reason) => counts[reason] > 0)
      .map((reason) =>
        m.coverage_count({
          label: LABELS[reason](),
          count: count(counts[reason]),
        }),
      )
      .join("\n"),
  );
</script>

<span
  role="img"
  aria-label={title}
  {title}
  class={cn("flex overflow-hidden rounded-xs bg-muted", className)}
>
  {#each parts as reason (reason)}
    <Tone
      {reason}
      class="block-full"
      style="inline-size: {(counts[reason] / Math.max(1, counts.total)) * 100}%"
    />
  {/each}
</span>
