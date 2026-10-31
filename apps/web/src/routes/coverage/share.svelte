<script lang="ts">
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import type { Snippet } from "svelte";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, percent } from "#lib/format.js";
  import type { Goal, Reason } from "#lib/coverage.js";
  import { covered, REASONS, label as reasonLabel } from "#lib/coverage.js";
  import { m } from "#lib/paraglide/messages.js";
  import Tone from "./tone.svelte";

  const {
    counts,
    goal,
    polity = "us",
    labels = {},
    strict = false,
    format = (value) => value,
    detail,
    detailFirst = false,
    children,
    class: className,
    detailClass,
  }: {
    counts: Partial<Record<Reason, number>> & { total: number };
    goal: Goal;
    polity?: string;
    labels?: Partial<Record<Reason, () => string>>;
    strict?: boolean;
    format?: (value: string) => string;
    detail?: (inputs: { complete: string; total: string }) => string;
    detailFirst?: boolean;
    children?: Snippet;
    class?: string;
    detailClass?: string;
  } = $props();

  const tether = TooltipPrimitive.createTether<null>();
  const value = $derived(covered(counts, strict));
  const parts = $derived(
    [
      ...new Set<Reason>([
        ...REASONS[goal],
        ...REASONS.precinct,
        ...REASONS.shapes,
      ]),
    ].filter((reason) => (counts[reason] ?? 0) > 0),
  );
</script>

{#snippet number()}
  <Tooltip.Trigger {tether} payload={null} tabindex={-1}>
    {#snippet child({ props })}
      <span {...props} class={className}
        >{format(percent(value / Math.max(1, counts.total), 1))}</span
      >
    {/snippet}
  </Tooltip.Trigger>
{/snippet}

{#snippet ratio()}
  {#if detail != null}
    <Tooltip.Trigger {tether} payload={null} tabindex={-1}>
      {#snippet child({ props })}
        <span {...props} class={detailClass}
          >{detail({
            complete: count(value),
            total: count(counts.total),
          })}</span
        >
      {/snippet}
    </Tooltip.Trigger>
  {/if}
{/snippet}

{#if detailFirst}
  {@render ratio()}
  {@render number()}
{:else}
  {@render number()}
  {@render children?.()}
  {@render ratio()}
{/if}

<Tooltip.Root {tether} delayDuration={80}>
  <Tooltip.Content
    collisionPadding={8}
    class="grid grid-cols-[0.625rem_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-0.5 leading-snug max-inline-80"
  >
    <span class="col-span-full font-semibold tabular-nums"
      >{(strict ? m.coverage_tip_complete : m.coverage_tip_held)({
        count: count(value),
        total: count(counts.total),
      })}</span
    >
    <span class="col-span-full pbe-1"
      >{(strict
        ? m.coverage_tip_complete_def
        : m.coverage_tip_held_def)()}</span
    >
    {#each parts as reason (reason)}
      <Tone {reason} class="rounded-xs block-2.5 inline-2.5" />
      <span
        >{(labels[reason] ?? (() => reasonLabel(goal, reason, polity)))()}</span
      >
      <span class="text-end tabular-nums"
        >{`${count(counts[reason] ?? 0)} · ${percent((counts[reason] ?? 0) / Math.max(1, counts.total), 1)}`}</span
      >
    {/each}
  </Tooltip.Content>
</Tooltip.Root>
