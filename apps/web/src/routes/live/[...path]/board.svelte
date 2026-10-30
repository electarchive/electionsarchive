<script lang="ts">
  import { resolve } from "$app/paths";
  import { flip } from "svelte/animate";
  import { prefersReducedMotion } from "svelte/motion";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Figure from "./figure.svelte";

  const {
    race,
    tones,
    votes,
    previous,
    isCertified,
    isFresh,
    ghost,
    variant,
  }: {
    race: {
      path: string;
      name: string;
      options: Array<{
        id: string;
        name: string;
        party: string | null;
        key: string;
        color: string | null;
        kind: string;
      }>;
      certified: { votes: Array<number>; elected: Array<boolean> } | null;
    };
    tones: Array<string>;
    votes: Array<number> | null;
    previous: Array<number> | null;
    isCertified: boolean;
    isFresh: boolean;
    ghost: boolean;
    variant: "lead" | "compact" | "measure";
  } = $props();

  const total = $derived.by(() => {
    let sum = 0;
    for (const value of votes ?? []) {
      sum += value;
    }
    return sum;
  });
  const ghostTotal = $derived.by(() => {
    let sum = 0;
    for (const value of race.certified?.votes ?? []) {
      sum += value;
    }
    return sum;
  });
  const rows = $derived(
    race.options
      .map((option, index) => {
        const own = votes?.[index] ?? 0;
        const final = race.certified?.votes[index] ?? 0;
        return {
          ...option,
          votes: own,
          share: total > 0 ? own / total : 0,
          delta: previous == null ? 0 : own - (previous[index] ?? 0),
          final: ghostTotal > 0 ? final / ghostTotal : null,
          isElected: isCertified && race.certified?.elected[index] === true,
          order: final,
          tone: tones[index] ?? "var(--muted-foreground)",
        };
      })
      .filter((row) => row.kind !== "write_in" || row.votes > 0)
      .toSorted((a, b) => b.votes - a.votes || b.order - a.order),
  );
  const shown = $derived(
    variant === "lead"
      ? rows.filter((row, index) => index < 3 || row.share >= 0.01)
      : rows.slice(0, 3),
  );
  const yes = $derived(
    rows.find((row) => row.name.toLowerCase() === "yes") ?? null,
  );
  const no = $derived(
    rows.find((row) => row.name.toLowerCase() === "no") ?? null,
  );
  const duration = $derived(prefersReducedMotion.current ? 0 : 420);
</script>

{#snippet status()}
  {#if votes == null || total === 0}
    <span class="text-muted-foreground">{m.live_waiting()}</span>
  {:else if isCertified}
    <span>{m.live_certified_count()}</span>
  {:else}
    <span class="text-muted-foreground"
      >{m.live_votes_counted({ count: count(total) })}</span
    >
  {/if}
{/snippet}

{#if variant === "measure"}
  <div class="grid gap-2">
    <div class="flex items-baseline justify-between gap-3">
      <a
        href={resolve(`/${race.path}`)}
        class="line-clamp-2 text-[0.9375rem] leading-snug font-semibold hover:underline"
        >{race.name.replace(/^.*?ballot question: /u, "")}</a
      >
    </div>
    <div
      class="relative overflow-clip rounded-xs bg-muted block-3"
      aria-hidden="true"
    >
      {#if total > 0}
        <div
          class="absolute inset-s-0 inset-bs-0 inset-be-0 bg-(--yes) transition-[width] duration-700 ease-out motion-reduce:transition-none"
          style:width="{(yes?.share ?? 0) * 100}%"
        ></div>
        <div
          class="absolute inset-e-0 inset-bs-0 inset-be-0 bg-(--no) transition-[width] duration-700 ease-out motion-reduce:transition-none"
          style:width="{(no?.share ?? 0) * 100}%"
        ></div>
      {/if}
      <div
        class="absolute inset-s-1/2 inset-bs-0 inset-be-0 -translate-x-1/2 bg-card inline-px"
      ></div>
      {#if ghost && yes?.final != null}
        <div
          class="absolute -inset-bs-0.5 -inset-be-0.5 -translate-x-1/2 bg-foreground inline-0.5"
          style:inset-inline-start="{yes.final * 100}%"
          title={m.live_certified_tick()}
        ></div>
      {/if}
    </div>
    <div
      class="flex items-baseline justify-between gap-3 font-figures text-sm tabular-nums"
    >
      <span class="font-semibold text-(--yes)"
        >{yes?.name ?? ""}
        {#if total > 0}<Figure
            value={yes?.share ?? 0}
            format={(value: number) => percent(value)}
          />{/if}</span
      >
      <span class="text-xs text-muted-foreground">{@render status()}</span>
      <span class="font-semibold text-(--no)"
        >{#if total > 0}<Figure
            value={no?.share ?? 0}
            format={(value: number) => percent(value)}
          />{/if}
        {no?.name ?? ""}</span
      >
    </div>
  </div>
{:else}
  <div
    class={cn("grid", {
      "gap-1": variant === "compact",
      "gap-3": variant === "lead",
    })}
  >
    {#if variant === "compact"}
      <div class="flex items-baseline justify-between gap-3">
        <a
          href={resolve(`/${race.path}`)}
          class="text-[0.9375rem] leading-snug font-semibold hover:underline"
          >{race.name}</a
        >
        {#if isFresh}
          <span
            class="shrink-0 animate-[fresh_1.6s_ease-out_both] rounded-full bg-foreground block-1.5 inline-1.5 motion-reduce:animate-none"
            aria-hidden="true"
          ></span>
        {/if}
      </div>
    {/if}
    <ol
      class={cn("grid", {
        "gap-1.5": variant === "compact",
        "gap-4": variant === "lead",
      })}
    >
      {#each shown as row, index (row.id)}
        <li animate:flip={{ duration }} class="grid gap-1">
          <div class="flex items-baseline gap-2">
            <span
              class={cn("grow truncate min-inline-0", {
                "text-[1.0625rem] font-semibold": variant === "lead",
                "text-sm": variant === "compact",
              })}
            >
              {row.name}
              {#if row.isElected}
                <span
                  class="ms-1 inline-block rounded-xs bg-foreground px-1 py-px align-[0.1em] text-[0.6875rem] font-semibold text-background"
                  >{m.live_elected()}</span
                >
              {:else if index === 0 && total > 0 && !isCertified && variant === "lead"}
                <span
                  class="ms-1 text-[0.8125rem] font-normal text-muted-foreground"
                  >{m.live_leading()}</span
                >
              {/if}
            </span>
            {#if variant === "lead" && row.delta > 0 && isFresh}
              {#key row.delta}
                <span
                  class="animate-[fresh_2.4s_ease-out_both] font-figures text-xs text-muted-foreground tabular-nums motion-reduce:animate-none max-sm:hidden"
                  >+{count(row.delta)}</span
                >
              {/key}
            {/if}
            <span
              class={cn("font-figures tabular-nums", {
                "text-[1.75rem] leading-none font-[760] tracking-[-0.02em]":
                  variant === "lead",
                "text-sm font-semibold": variant === "compact",
              })}
            >
              {#if total > 0}<Figure
                  value={row.share}
                  format={(value: number) => percent(value)}
                />{:else}–{/if}
            </span>
          </div>
          <div
            class={cn("relative rounded-xs bg-muted", {
              "block-2.5": variant === "lead",
              "block-1.5": variant === "compact",
            })}
          >
            <div
              class="absolute inset-s-0 inset-bs-0 inset-be-0 rounded-xs transition-[width] duration-700 ease-out motion-reduce:transition-none"
              style:width="{row.share * 100}%"
              style:background-color={row.tone}
            ></div>
            <div
              class="absolute inset-s-1/2 -inset-bs-0.5 -inset-be-0.5 bg-foreground/25 inline-px"
              aria-hidden="true"
            ></div>
            {#if ghost && row.final != null && !isCertified}
              <div
                class="absolute -inset-bs-1 -inset-be-1 -translate-x-1/2 bg-foreground inline-0.5"
                style:inset-inline-start="{row.final * 100}%"
                title={m.live_certified_tick()}
              ></div>
            {/if}
          </div>
          {#if variant === "lead"}
            <div
              class="flex items-baseline justify-between gap-3 text-[0.8125rem] text-muted-foreground"
            >
              <span class="truncate">{row.party ?? ""}</span>
              <span class="font-figures tabular-nums"
                ><Figure
                  value={row.votes}
                  format={(value: number) => count(Math.round(value))}
                /></span
              >
            </div>
          {/if}
        </li>
      {/each}
    </ol>
    {#if variant === "compact"}
      <p class="text-xs">{@render status()}</p>
    {/if}
  </div>
{/if}

<style>
  @keyframes -global-fresh {
    0% {
      background-color: var(--marker);
      opacity: 1;
    }
    60% {
      opacity: 1;
    }
    100% {
      opacity: 0.6;
    }
  }
</style>
