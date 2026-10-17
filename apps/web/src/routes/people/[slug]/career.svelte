<script lang="ts">
  import Export from "#lib/components/export.svelte";
  import { percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";

  const { record }: { record: PageData["person"]["record"] } = $props();

  const PAD = { top: 14, right: 16, bottom: 26, left: 34 };
  const OUTCOMES: Record<string, () => string> = {
    elected: m.outcome_elected,
    won: m.outcome_won,
    advanced: m.outcome_advanced,
    tied: m.outcome_tied,
    lost: m.person_lost_mark,
  };

  let width = $state(720);
  let hovered = $state<number | null>(null);

  const marks = $derived(
    record
      .map((entry) => ({
        ...entry,
        at:
          entry.year +
          (Date.parse(`${entry.date}T00:00:00Z`) -
            Date.UTC(Number(entry.date.slice(0, 4)), 0, 1)) /
            31_622_400_000,
        share:
          entry.votes == null || entry.total == null || entry.total === 0
            ? null
            : entry.votes / entry.total,
        isWon:
          entry.outcome === "elected" ||
          entry.outcome === "won" ||
          entry.outcome === "advanced",
        tint: swatch(
          entry.party?.color ?? null,
          entry.party == null ? "" : `party:${entry.party.id}`,
        ),
      }))
      .toSorted((a, b) => a.at - b.at),
  );
  const range = $derived.by(() => {
    const low = Math.min(...marks.map((mark) => mark.at));
    const high = Math.max(...marks.map((mark) => mark.at));
    const pad = Math.max(1, (high - low) * 0.04);
    return { low: low - pad, high: high + pad };
  });
  const top = $derived(
    Math.min(
      1,
      Math.ceil(Math.max(0.5, ...marks.map((mark) => mark.share ?? 0)) * 10) /
        10,
    ),
  );
  const height = $derived(
    Math.round(Math.min(280, Math.max(190, width * 0.32))),
  );
  const sx = (value: number) =>
    PAD.left +
    ((value - range.low) / (range.high - range.low)) *
      (width - PAD.left - PAD.right);
  const sy = (value: number) =>
    PAD.top + (1 - value / top) * (height - PAD.top - PAD.bottom);
  const years = $derived.by(() => {
    const span = range.high - range.low;
    const step = span > 40 ? 10 : span > 16 ? 4 : span > 6 ? 2 : 1;
    const found: Array<number> = [];
    for (
      let year = Math.ceil(range.low / step) * step;
      year <= range.high;
      year += step
    ) {
      found.push(year);
    }
    return found;
  });
  const levels = $derived(
    [0, 0.25, 0.5, 0.75, 1].filter((level) => level <= top),
  );
  const generals = $derived(
    marks.filter((mark) => mark.share != null && mark.kind !== "primary"),
  );
  const lit = $derived(hovered == null ? null : (marks[hovered] ?? null));
</script>

{#if marks.filter((mark) => mark.share != null).length > 1}
  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="career-title"
    data-export
  >
    <Export name="career" class="absolute inset-e-2 inset-bs-2" />
    <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 pe-8">
      <div class="grid gap-0.5">
        <h2 id="career-title" class="heading">
          {m.person_career_title()}
        </h2>
        <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
          {m.person_career_lede()}
        </p>
      </div>
      <p class="flex gap-4 text-xs text-muted-foreground">
        <span class="flex items-center gap-1.5"
          ><span class="rounded-full bg-foreground block-2.5 inline-2.5"
          ></span>{m.person_won_mark()}</span
        >
        <span class="flex items-center gap-1.5"
          ><span
            class="rounded-full border-2 border-foreground block-2.5 inline-2.5"
          ></span>{m.person_lost_mark()}</span
        >
        {#if marks.some((mark) => mark.kind === "primary")}
          <span class="flex items-center gap-1.5"
            ><span class="rotate-45 border-2 border-foreground block-2 inline-2"
            ></span>{m.polity_filter_primary()}</span
          >
        {/if}
      </p>
    </div>
    <div class="relative min-inline-0" bind:clientWidth={width}>
      <svg
        class="block overflow-visible block-auto max-inline-full"
        viewBox="0 0 {width} {height}"
        {width}
        {height}
        role="img"
        aria-label={m.person_career_title()}
      >
        {#each levels as level (level)}
          <line
            x1={PAD.left}
            x2={width - PAD.right}
            y1={sy(level)}
            y2={sy(level)}
            class="stroke-border"
            stroke-dasharray={level === 0.5 ? "3 3" : undefined}
          />
          <text
            x={PAD.left - 6}
            y={sy(level) + 3.5}
            text-anchor="end"
            class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
            >{Math.round(level * 100)}</text
          >
        {/each}
        {#each years as year (year)}
          <text
            x={sx(year)}
            y={height - 8}
            text-anchor="middle"
            class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
            >{year}</text
          >
        {/each}
        <polyline
          points={generals
            .map((mark) => `${sx(mark.at)},${sy(mark.share ?? 0)}`)
            .join(" ")}
          fill="none"
          class="stroke-muted-foreground/50"
          stroke-width="1.25"
        />
        {#each marks as mark, index (index)}
          {#if mark.share != null}
            {const x = $derived(sx(mark.at))}
            {const y = $derived(sy(mark.share))}
            {#if mark.kind === "primary"}
              <rect
                x={x - 4}
                y={y - 4}
                width="8"
                height="8"
                transform="rotate(45 {x} {y})"
                fill={mark.isWon ? mark.tint : "var(--card)"}
                stroke={mark.tint}
                stroke-width="2"
              />
            {:else}
              <circle
                cx={x}
                cy={y}
                r={hovered === index ? 6.5 : 5}
                fill={mark.isWon ? mark.tint : "var(--card)"}
                stroke={mark.tint}
                stroke-width="2"
              />
            {/if}
            <rect
              x={x - 9}
              y={y - 9}
              width="18"
              height="18"
              fill="transparent"
              role="presentation"
              onpointerenter={() => (hovered = index)}
              onpointerleave={() => (hovered = null)}
            />
          {/if}
        {/each}
      </svg>
      {#if lit != null}
        <div
          class="pointer-events-none absolute grid gap-0.5 rounded-sm bg-card/95 px-2.5 py-1.5 text-xs shadow-menu ring-1 ring-border max-inline-64"
          style:inset-inline-start="{Math.max(
            0,
            Math.min(width - 256, sx(lit.at) + 12),
          )}px"
          style:inset-block-start="{Math.max(0, sy(lit.share ?? 0) - 64)}px"
        >
          <span class="text-[0.8125rem] font-semibold">{lit.name}</span>
          {#if lit.party != null}
            <span class="text-muted-foreground">{lit.party.label}</span>
          {/if}
          <span class="flex justify-between gap-4">
            <span class="font-figures font-semibold tabular-nums"
              >{lit.share == null ? "–" : percent(lit.share)}</span
            >
            {#if lit.outcome != null}
              <span class="font-semibold"
                >{OUTCOMES[lit.outcome]?.() ?? lit.outcome}</span
              >
            {/if}
          </span>
        </div>
      {/if}
    </div>
  </section>
{/if}
