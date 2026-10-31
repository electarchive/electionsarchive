<script lang="ts">
  import type { Snippet } from "svelte";
  import Name from "#lib/components/name.svelte";
  import { count, initials, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Bar from "./bar.svelte";

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

  const present = $derived(parts.filter((part) => part.value > 0));
  const shown = $derived(
    present
      .toSorted((a, b) => b.value - a.value)
      .slice(0, 2)
      .toSorted((a, b) => parts.indexOf(a) - parts.indexOf(b)),
  );
  const isPictured = $derived(shown.some((part) => part.photo != null));
  const figure = (value: number) => (isCount ? count(value) : percent(value));
</script>

{#snippet label(part: (typeof parts)[number], isFull: boolean)}
  <span
    class={cn(
      "mbs-[-0.3em] mbe-[-0.3em] block pbs-[0.3em] pbe-[0.3em] text-[0.9375rem]/tight font-semibold [text-box:trim-both_cap_alphabetic]",
      { "line-clamp-2 text-balance": isFull, truncate: !isFull },
    )}
  >
    {#if part.photo == null && present.length > 2 && !(isPictured && part.person != null)}
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
            {:else if isPictured && part.person != null}
              <span
                class={cn(
                  "grid shrink-0 place-items-center rounded-full bg-secondary font-semibold text-muted-foreground ring-2 ring-(--party) ring-offset-2 ring-offset-card select-none",
                  {
                    "text-[0.9375rem] block-12 inline-12 sm:text-base sm:block-14 sm:inline-14":
                      isLead,
                    "text-[0.875rem] block-11 inline-11": !isLead,
                  },
                )}
                aria-hidden="true">{initials(part.label)}</span
              >
            {/if}
            <span class="grid gap-2 min-inline-0">
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
              {@render label(
                part,
                isLead &&
                  (part.photo != null || (isPictured && part.person != null)),
              )}
            </span>
          </span>
        </Name>
      {/each}
    </div>
    <Bar {parts} {total} {isCount} {isLead} {isWhole} {isLabeled} {segments} />
  </div>
{/if}
