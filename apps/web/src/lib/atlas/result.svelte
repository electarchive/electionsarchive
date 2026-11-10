<script lang="ts">
  import type { Snippet } from "svelte";
  import XIcon from "@lucide/svelte/icons/x";
  import { count, numbers, percent, points, precision } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    summary,
    year,
    onclear,
    actions = [],
    layout = "full",
    isApart = false,
    opening,
    headline,
  }: {
    summary: {
      name: string;
      kind: string;
      valid: number;
      previous: number | null;
      isExact: boolean;
      isCombined: boolean;
      rows: Array<{
        key: string;
        label: string;
        tone: string;
        share: number;
        votes: number | null;
        change: number | null;
        isLeader: boolean;
      }>;
      verdict: {
        label: string;
        tone: string;
        margin: number;
        then: {
          label: string;
          tone: string;
          margin: number;
          isFlipped: boolean;
        } | null;
      } | null;
    };
    year: string | null;
    onclear: (() => void) | null;
    actions?: Array<{ label: string; href?: string; onclick?: () => void }>;
    layout?: "full" | "peek" | "head" | "body";
    isApart?: boolean;
    opening?: Snippet;
    headline?: Snippet;
  } = $props();

  function gap(margin: number) {
    const digits = precision(margin);
    return numbers({
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(Math.abs(margin) * 100);
  }

  function tally(votes: number) {
    return summary.isExact
      ? count(Math.round(votes))
      : numbers({ notation: "compact", maximumSignificantDigits: 3 }).format(
          votes,
        );
  }

  let isAll = $state(false);
  const LIMIT = 6;
  const scale = $derived.by(() => {
    let widest = 0.5;
    for (const row of summary.rows) {
      widest = Math.max(widest, row.share, row.share - (row.change ?? 0));
    }
    return widest;
  });
  const rows = $derived(isAll ? summary.rows : summary.rows.slice(0, LIMIT));
  const growth = $derived(
    summary.previous == null || summary.previous === 0 || summary.valid === 0
      ? null
      : summary.valid / summary.previous - 1,
  );
  const lead = $derived(
    summary.rows.length > 1
      ? (summary.rows[0]?.share ?? 0) - (summary.rows[1]?.share ?? 0)
      : null,
  );
</script>

<div class="grid grid-cols-[minmax(0,1fr)] gap-3">
  {#if layout !== "body"}
    <header class="grid gap-0.5">
      <div class="flex items-start gap-2">
        <h2 class="flex-1 text-lg/6 font-[720] text-balance" data-scope-title>
          {summary.name}
        </h2>
        {#if onclear != null}
          <button
            type="button"
            class="-me-1.5 grid shrink-0 place-items-center rounded-xs text-muted-foreground block-7 inline-7 hover:bg-secondary hover:text-foreground"
            aria-label={m.atlas_clear_selection()}
            title={m.atlas_clear_selection()}
            onclick={onclear}><XIcon class="block-4 inline-4" /></button
          >
        {/if}
      </div>
      <p class="flex flex-wrap gap-x-3 text-[0.8125rem] text-muted-foreground">
        <span>{summary.kind}</span>
        {#if summary.valid > 0}
          <span class="font-figures tabular-nums"
            >{m.atlas_votes({ votes: count(summary.valid) })}</span
          >
        {/if}
      </p>
    </header>
  {/if}

  {#if opening != null && (layout === "full" || layout === "body")}
    {@render opening()}
  {/if}

  {#if summary.verdict != null && (layout === "full" || layout === "body")}
    {const verdict = $derived(summary.verdict)}
    <div class="grid gap-1 border-s-3 ps-2.5" style:border-color={verdict.tone}>
      <p class="text-[0.9375rem]/5 font-semibold text-pretty">
        {m.atlas_verdict({ name: verdict.label, points: gap(verdict.margin) })}
      </p>
      {#if verdict.then != null && year != null}
        <p
          class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.8125rem] text-muted-foreground"
        >
          <span
            >{m.atlas_verdict_then({
              name: verdict.then.label,
              points: gap(verdict.then.margin),
              year,
            })}</span
          >
          {#if verdict.then.isFlipped}
            <span
              class="rounded-xs px-1.5 text-xs/5 font-semibold text-card"
              style:background={verdict.tone}>{m.map_flipped()}</span
            >
          {/if}
        </p>
      {/if}
      {#if growth != null && year != null && Math.abs(growth) >= 0.0005}
        <p class="text-[0.8125rem] text-muted-foreground">
          {growth > 0
            ? m.atlas_votes_more({ percent: percent(growth), year })
            : m.atlas_votes_fewer({ percent: percent(-growth), year })}
        </p>
      {/if}
    </div>
  {/if}

  {#if summary.rows.length > 0 && layout !== "head" && !(isApart && layout !== "peek")}
    {#if layout === "peek" && headline != null}
      {@render headline()}
    {:else if layout === "peek"}
      {const leader = $derived(summary.rows[0])}
      {#if leader != null}
        <p class="flex items-center gap-2 text-[0.9375rem]">
          <span
            class="shrink-0 rounded-xs block-3 inline-3"
            style:background={leader.tone}
          ></span>
          <span class="truncate font-semibold">{leader.label}</span>
          <span class="font-figures tabular-nums">{percent(leader.share)}</span>
          {#if lead != null}
            <span
              class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
              >{points(lead)}</span
            >
          {/if}
        </p>
      {/if}
    {:else}
      <ul class="grid grid-cols-[minmax(0,1fr)] gap-2">
        {#each rows as row (row.key)}
          <li class="grid grid-cols-[minmax(0,1fr)] gap-1">
            <div class="flex items-baseline gap-2 text-[0.8125rem]">
              <span
                class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
                style:background={row.tone}
              ></span>
              <span
                class={cn("flex-1 truncate", { "font-semibold": row.isLeader })}
                >{row.label}</span
              >
              {#if row.votes != null}
                <span
                  class="font-figures text-xs text-muted-foreground tabular-nums"
                  >{tally(row.votes)}</span
                >
              {/if}
              {#if row.change != null}
                <span
                  class="font-figures text-xs text-muted-foreground tabular-nums"
                  title={year == null ? undefined : m.atlas_since({ year })}
                  >{points(row.change)}</span
                >
              {/if}
              <span
                class="text-end font-figures font-semibold tabular-nums inline-12"
                >{percent(row.share)}</span
              >
            </div>
            <div class="relative rounded-xs bg-secondary block-1.5">
              <div
                class="absolute inset-y-0 inset-s-0 rounded-xs"
                style:background={row.tone}
                style:inline-size="{(row.share / scale) * 100}%"
              ></div>
              {#if row.change != null}
                {const then = $derived(row.share - row.change)}
                <div
                  class="absolute -inset-y-0.5 -translate-x-1/2 bg-foreground inline-0.5 rtl:translate-x-1/2"
                  style:inset-inline-start="{(Math.max(then, 0) / scale) *
                    100}%"
                  aria-hidden="true"
                ></div>
              {/if}
            </div>
          </li>
        {/each}
      </ul>
      {#if summary.rows.length > LIMIT}
        <button
          type="button"
          class="justify-self-start text-[0.8125rem] font-semibold text-muted-foreground underline decoration-border underline-offset-3 hover:text-foreground"
          onclick={() => (isAll = !isAll)}
          >{isAll
            ? m.atlas_fewer()
            : m.atlas_all_rows({ count: summary.rows.length })}</button
        >
      {/if}
      {#if summary.isCombined}
        <p class="text-xs text-pretty text-muted-foreground">
          {m.atlas_combined_note()}
        </p>
      {/if}
    {/if}
  {/if}

  {#if actions.length > 0 && (layout === "full" || layout === "body")}
    <div class="flex flex-wrap gap-1.5">
      {#each actions as action (action.label)}
        {#if action.href != null}
          <a
            href={action.href}
            class="flex items-center rounded-sm px-2.5 text-[0.8125rem] font-semibold ring-1 ring-input block-8 hover:bg-secondary"
            >{action.label}</a
          >
        {:else}
          <button
            type="button"
            class="flex items-center rounded-sm px-2.5 text-[0.8125rem] font-semibold ring-1 ring-input block-8 hover:bg-secondary"
            onclick={action.onclick}>{action.label}</button
          >
        {/if}
      {/each}
    </div>
  {/if}
</div>
