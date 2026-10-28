<script lang="ts">
  import type { Snippet } from "svelte";
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    parts,
    total,
    isCount,
    isLead = false,
    isSlim = false,
    isWhole = false,
    isLabeled = false,
    segments,
    class: className,
  }: {
    parts: Array<{
      key: string;
      label: string;
      color: string;
      value: number;
      detail?: string | null;
    }>;
    total: number;
    isCount: boolean;
    isLead?: boolean;
    isSlim?: boolean;
    isWhole?: boolean;
    isLabeled?: boolean;
    segments?: Snippet;
    class?: string;
  } = $props();

  const tether = TooltipPrimitive.createTether<string>();
  const present = $derived(parts.filter((part) => part.value > 0));
  const order = $derived.by(() => {
    const shown = present
      .toSorted((a, b) => b.value - a.value)
      .slice(0, 2)
      .toSorted((a, b) => parts.indexOf(a) - parts.indexOf(b));
    const between = present.filter((part) => !shown.includes(part));
    const tiny = between.filter(
      (part) => part.value / Math.max(total, Number.EPSILON) < 0.004,
    );
    if (tiny.length < 2) {
      return [...shown.slice(0, 1), ...between, ...shown.slice(1)];
    }
    let value = 0;
    for (const part of tiny) {
      value += part.value;
    }
    return [
      ...shown.slice(0, 1),
      ...between.filter((part) => !tiny.includes(part)),
      {
        key: "balance:others",
        label: m.regions_others(),
        color: "var(--muted-foreground)",
        value,
        members: tiny.length,
      },
      ...shown.slice(1),
    ];
  });
  const rest = $derived.by(() => {
    let sum = 0;
    for (const part of present) {
      sum += part.value;
    }
    return Math.max(0, total - sum);
  });
  const majority = $derived(Math.floor(total / 2) + 1);
  const figure = (value: number) => (isCount ? count(value) : percent(value));
</script>

<div class={cn("relative", { "pbe-5": isWhole && isLabeled }, className)}>
  <div
    class={cn(
      "flex animate-sweep gap-px overflow-hidden rounded-xs motion-reduce:animate-none",
      {
        "block-4 sm:block-5": isLead,
        "block-3": !isLead && !isSlim,
        "block-2": isSlim,
      },
    )}
    role="img"
    aria-label={present
      .map((part) => `${part.label} ${figure(part.value)}`)
      .join(", ")}
  >
    {#if segments == null}
      {#each order as part (part.key)}
        <Tooltip.Trigger {tether} payload={part.key} tabindex={-1}>
          {#snippet child({ props })}
            <span
              {...props}
              class="block shrink-0 basis-0 bg-(--party) transition-opacity hover:opacity-80"
              style:--party={part.color}
              style:flex-grow={part.value}
            ></span>
          {/snippet}
        </Tooltip.Trigger>
      {/each}
      {#if rest > 0}
        <span class="block basis-0 bg-muted" style:flex-grow={rest}></span>
      {/if}
    {:else}
      {@render segments()}
    {/if}
  </div>
  {#if isWhole && total > 1}
    <span
      class={cn(
        "absolute -inset-bs-1 -translate-x-1/2 bg-foreground inline-0.5",
        {
          "block-[calc(100%+0.5rem)]": !isLabeled,
          "block-[calc(100%-0.75rem)]": isLabeled,
        },
      )}
      style:inset-inline-start="{Math.min(1, majority / total) * 100}%"
      aria-hidden="true"
    ></span>
    {#if isLabeled}
      <span
        class="absolute inset-be-0 -translate-x-1/2 text-[0.8125rem] whitespace-nowrap text-muted-foreground"
        style:inset-inline-start="{Math.min(1, majority / total) * 100}%"
        >{m.electors_majority({ count: count(majority) })}</span
      >
    {/if}
  {/if}
</div>
<Tooltip.Root {tether} delayDuration={120}>
  {#snippet children({ payload })}
    {const part = $derived(order.find((entry) => entry.key === payload))}
    {#if part != null}
      <Tooltip.Content
        collisionPadding={8}
        class="grid gap-0.5 leading-snug inline-[min(16rem,calc(100vw-2rem))]"
      >
        <span class="flex items-baseline gap-1.5">
          <span
            class="shrink-0 self-center rounded-xs block-2 inline-2"
            style:background-color={part.color}
          ></span>
          <span class="font-semibold">{part.label}</span>
        </span>
        {#if "members" in part}
          <span class="text-xs opacity-80"
            >{m.balance_others_count({ count: count(part.members) })}</span
          >
        {:else if part.detail != null}
          <span class="text-xs opacity-80">{part.detail}</span>
        {/if}
        <span
          class="flex items-baseline justify-between gap-3 font-figures text-xs tabular-nums"
          >{isCount
            ? m.series_electoral({
                votes: count(part.value),
                total: count(total),
              })
            : percent(part.value)}{#if isCount}<span class="opacity-70"
              >{percent(part.value / Math.max(total, 1))}</span
            >{/if}</span
        >
      </Tooltip.Content>
    {/if}
  {/snippet}
</Tooltip.Root>
