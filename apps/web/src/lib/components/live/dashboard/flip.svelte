<script lang="ts">
  import ArrowRightIcon from "@lucide/svelte/icons/arrow-right";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    flip,
    from,
    to,
    isLabeled = false,
    isInverse = false,
    isCompact = false,
  }: {
    flip: "flipped" | "leading";
    from: { short: string; color: string } | null;
    to: { short: string; color: string };
    isLabeled?: boolean;
    isInverse?: boolean;
    isCompact?: boolean;
  } = $props();

  const about = $derived(
    flip === "flipped"
      ? m.night_flip_flipped_about({ from: from?.short ?? "–", to: to.short })
      : m.night_flip_leading_about({ from: from?.short ?? "–", to: to.short }),
  );
  const isFlipped = $derived(flip === "flipped");
</script>

{#snippet party(found: { short: string; color: string })}
  {const name = $derived(
    isCompact ? (Array.from(found.short)[0] ?? "") : found.short,
  )}
  {#if isInverse}
    <span class="inline-flex items-center gap-0.5"
      ><span
        class="rounded-full ring-1 ring-current block-1.5 inline-1.5"
        style:background-color={found.color}
      ></span>{name}</span
    >
  {:else}
    <span
      style:color="color-mix(in oklab, {found.color} 80%, var(--foreground))"
      >{name}</span
    >
  {/if}
{/snippet}

<span
  class={cn(
    "inline-flex shrink-0 items-center gap-0.5 rounded-xs px-1 font-figures text-[11px]/4 font-[760] whitespace-nowrap",
    { "border border-dashed": !isFlipped },
    { "border-current/70": !isFlipped && isInverse },
    { "shadow-[inset_0_0_0_1px_currentColor]": isFlipped && isInverse },
  )}
  style:border-color={!isFlipped && !isInverse
    ? `color-mix(in oklab, ${to.color} 70%, var(--background))`
    : undefined}
  style:background-color={isFlipped && !isInverse
    ? `color-mix(in oklab, ${to.color} 15%, var(--background))`
    : undefined}
  style:box-shadow={isFlipped && !isInverse
    ? `inset 0 0 0 1px color-mix(in oklab, ${to.color} 55%, var(--background))`
    : undefined}
  title={about}
  ><span aria-hidden="true" class="contents"
    >{#if from != null}{@render party(from)}{/if}<ArrowRightIcon
      class={cn("shrink-0 block-2.5 inline-2.5", {
        "text-muted-foreground": !isInverse,
      })}
      strokeWidth={3}
    />{@render party(to)}{#if isLabeled}<span
        class="ms-1 font-sans font-semibold text-foreground"
        >{isFlipped
          ? m.night_flip_status_flipped()
          : m.night_flip_status_leading()}</span
      >{/if}</span
  ><span class="sr-only">{about}</span></span
>
