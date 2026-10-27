<script lang="ts">
  import type { Snippet } from "svelte";
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import Name from "#lib/components/name.svelte";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    parts,
    total,
    isCount,
    isLead = false,
    isWhole = false,
    isLabeled = false,
    middle,
    segments,
  }: {
    parts: Array<{
      key: string;
      label: string;
      color: string;
      value: number;
      person?: string | null;
      party?: string | null;
      detail?: string | null;
      delta?: number | null;
      photo?: { src: string } | null;
    }>;
    total: number;
    isCount: boolean;
    isLead?: boolean;
    isWhole?: boolean;
    isLabeled?: boolean;
    middle?: Snippet;
    segments?: Snippet;
  } = $props();

  const tether = TooltipPrimitive.createTether<string>();
  const present = $derived(parts.filter((part) => part.value > 0));
  const shown = $derived(
    present
      .toSorted((a, b) => b.value - a.value)
      .slice(0, 2)
      .toSorted((a, b) => parts.indexOf(a) - parts.indexOf(b)),
  );
  const order = $derived([
    ...shown.slice(0, 1),
    ...present.filter((part) => !shown.includes(part)),
    ...shown.slice(1),
  ]);
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

{#snippet label(part: (typeof parts)[number], isFull: boolean)}
  <span
    class={cn(
      "mbe-[-0.3em] block pbe-[0.3em] text-[0.9375rem]/tight font-semibold [text-box:trim-both_cap_alphabetic]",
      { "line-clamp-2 text-balance": isFull, truncate: !isFull },
    )}
  >
    {#if part.photo == null && present.length > 2}
      <span
        class="me-1.5 inline-block rounded-xs bg-(--party) align-[-0.02em] block-[0.62em] inline-[0.62em]"
      ></span>
    {/if}<span
      class={cn({
        "underline decoration-transparent decoration-1 underline-offset-[0.22em] transition-[text-decoration-color] group-hover:decoration-current group-focus-visible:decoration-current":
          part.person != null || part.party != null,
      })}>{part.label}</span
    >
    {#if part.detail != null}
      <span class="font-normal text-muted-foreground max-sm:hidden"
        >{part.detail}</span
      >
    {/if}
    {#if part.delta !== undefined}
      <span
        class={cn("font-figures text-[0.8125rem] font-normal", {
          "text-muted-foreground": part.delta == null || part.delta === 0,
        })}
        >{part.delta == null
          ? m.seats_change_new()
          : part.delta === 0
            ? "±0"
            : `${part.delta > 0 ? "+" : "−"}${count(Math.abs(part.delta))}`}</span
      >
    {/if}
  </span>
{/snippet}

{#if !isCount && shown.length === 1 && shown[0] != null}
  <p class="flex items-baseline gap-1.5" style:--party={shown[0].color}>
    <Name
      class="group no-underline min-inline-0"
      label={shown[0].label}
      color={shown[0].color}
      person={shown[0].person}
      party={shown[0].party}
    >
      {@render label({ ...shown[0], photo: null }, false)}
    </Name>
    <span class="shrink-0 text-[0.8125rem] text-muted-foreground"
      >{m.map_unopposed()}</span
    >
  </p>
{:else}
  <div class="grid gap-2">
    <div class="flex items-end justify-between gap-4">
      {#each shown as part, at (part.key)}
        {#if at === 1}
          {@render middle?.()}
        {/if}
        <Name
          class={cn(
            "group flex items-center gap-3 no-underline min-inline-0 focus-visible:outline-offset-4",
            { "flex-row-reverse text-end": at === 1 },
          )}
          label={part.label}
          color={part.color}
          person={part.person}
          party={part.party}
        >
          <span class="contents" style:--party={part.color}>
            {#if part.photo != null}
              <img
                class={cn(
                  "shrink-0 rounded-full bg-muted object-cover object-top ring-2 ring-(--party) ring-offset-2 ring-offset-card",
                  {
                    "block-12 inline-12 sm:block-14 sm:inline-14": isLead,
                    "block-11 inline-11": !isLead,
                  },
                )}
                src={part.photo.src}
                alt=""
                width="56"
                height="56"
              />
            {/if}
            <span
              class={cn("grid min-inline-0", {
                "content-between gap-2.5": part.photo != null,
                "gap-2": part.photo == null,
                "min-block-12 sm:min-block-14": part.photo != null && isLead,
                "min-block-11": part.photo != null && !isLead,
              })}
            >
              <span
                class={cn(
                  "font-figures leading-none font-[780] tracking-[-0.02em] text-foreground [text-box:trim-both_cap_alphabetic]",
                  {
                    "text-[2rem] sm:text-[2.5rem]": isLead,
                    "text-[1.75rem]": !isLead,
                  },
                  { "ms-[-0.05em]": at === 0, "me-[-0.02em]": at === 1 },
                )}>{figure(part.value)}</span
              >
              {@render label(part, isLead && part.photo != null)}
            </span>
          </span>
        </Name>
      {/each}
    </div>
    <div class={cn("relative", { "pbe-5": isWhole && isLabeled })}>
      <div
        class={cn(
          "flex animate-sweep gap-px overflow-hidden rounded-xs motion-reduce:animate-none",
          { "block-4 sm:block-5": isLead, "block-3": !isLead },
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
  </div>
  <Tooltip.Root {tether} delayDuration={120}>
    {#snippet children({ payload })}
      {const part = $derived(present.find((entry) => entry.key === payload))}
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
          {#if part.detail != null}
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
{/if}
