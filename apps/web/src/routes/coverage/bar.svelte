<script lang="ts">
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { Counts, Goal, Reason } from "#lib/coverage.js";
  import { LABELS, REASONS } from "#lib/coverage.js";
  import Tone from "./tone.svelte";

  const {
    counts,
    goal,
    class: className,
  }: { counts: Counts; goal: Goal; class?: string } = $props();

  const tether = TooltipPrimitive.createTether<Reason>();
  const parts = $derived(REASONS[goal].filter((reason) => counts[reason] > 0));
</script>

<span
  role="img"
  aria-label={parts
    .map((reason) =>
      m.coverage_count({
        label: LABELS[reason](),
        count: count(counts[reason]),
      }),
    )
    .join(", ")}
  class={cn("flex overflow-hidden rounded-xs bg-muted", className)}
>
  {#each parts as reason (reason)}
    <Tooltip.Trigger {tether} payload={reason} tabindex={-1}>
      {#snippet child({ props })}
        <span
          {...props}
          class="block-full"
          style:inline-size="{(counts[reason] / Math.max(1, counts.total)) *
            100}%"
        >
          <Tone {reason} class="block block-full inline-full" />
        </span>
      {/snippet}
    </Tooltip.Trigger>
  {/each}
</span>

<Tooltip.Root {tether} delayDuration={80}>
  {#snippet children({ payload })}
    {const reason = $derived(parts.find((part) => part === payload))}
    {#if reason != null}
      <Tooltip.Content collisionPadding={8} class="grid gap-0.5 leading-snug">
        <span class="font-semibold">{LABELS[reason]()}</span>
        <span class="tabular-nums"
          >{count(counts[reason])} · {percent(
            counts[reason] / Math.max(1, counts.total),
            1,
          )}</span
        >
      </Tooltip.Content>
    {/if}
  {/snippet}
</Tooltip.Root>
