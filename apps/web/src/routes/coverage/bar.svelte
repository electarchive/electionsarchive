<script lang="ts">
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { Counts, Goal, Reason } from "#lib/coverage.js";
  import { REASONS, label as reasonLabel } from "#lib/coverage.js";
  import Tone from "./tone.svelte";

  const {
    counts,
    goal,
    polity = "us",
    population = false,
    class: className,
  }: {
    counts: Counts;
    goal: Goal;
    polity?: string;
    population?: boolean;
    class?: string;
  } = $props();

  const tether = TooltipPrimitive.createTether<
    Reason | "icpsr" | "published"
  >();
  const parts = $derived(
    REASONS[goal]
      .flatMap((reason): Array<Reason | "icpsr" | "published"> =>
        reason === "complete" ? [reason, "icpsr", "published"] : [reason],
      )
      .filter((part) => counts[part] > 0),
  );
  const label = (part: Reason | "icpsr" | "published") =>
    part === "icpsr"
      ? m.coverage_icpsr()
      : part === "published"
        ? m.coverage_published()
        : reasonLabel(goal, part, polity);
  const share = (part: Reason | "icpsr" | "published") =>
    percent(counts[part] / Math.max(1, counts.total), 1);
</script>

<span
  role="img"
  aria-label={parts
    .map((reason) =>
      m.coverage_count({
        label: label(reason),
        count: population
          ? m.coverage_population_share({ percent: share(reason) })
          : count(counts[reason]),
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
          style:inline-size="{((reason === 'complete'
            ? counts.complete - counts.icpsr - counts.published
            : counts[reason]) /
            Math.max(1, counts.total)) *
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
        <span class="font-semibold">{label(reason)}</span>
        <span class="tabular-nums"
          >{population
            ? m.coverage_population_share({ percent: share(reason) })
            : `${count(counts[reason])} · ${share(reason)}`}</span
        >
      </Tooltip.Content>
    {/if}
  {/snippet}
</Tooltip.Root>
