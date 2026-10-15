<script lang="ts">
  import { resolve } from "$app/paths";
  import { onMount } from "svelte";
  import { cubicOut } from "svelte/easing";
  import { Tween } from "svelte/motion";
  import type { LabView } from "./view.svelte.js";
  import ConstituencyDetail from "./constituency-detail.svelte";
  import ConstituencyMap from "./constituency-map.svelte";
  import { copy } from "./copy.js";
  import {
    count,
    majority,
    parties,
    partyColor,
    percent,
    seatOrder,
    summary,
    totalSeats,
    turnout,
    unseatedCount,
  } from "./election.js";
  import Hemicycle from "./hemicycle.svelte";
  import ResultsTable from "./results-table.svelte";
  import Specimen from "./specimen.svelte";

  const { view, motion }: { view: LabView; motion: boolean } = $props();

  const progress = new Tween(1, { duration: 900, easing: cubicOut });

  onMount(() => {
    if (!motion) return;
    void progress.set(0, { duration: 0 });
    void progress.set(1);
  });

  const stats = $derived([
    [copy.seats_label, String(Math.round(totalSeats * progress.current))],
    [copy.majority_label, String(Math.round(majority * progress.current))],
    [copy.turnout_label, percent(turnout * progress.current)],
    [
      copy.col_second,
      count.format(Math.round(summary.valid_second * progress.current)),
    ],
    [
      copy.registered_label,
      count.format(Math.round(summary.registered * progress.current)),
    ],
  ]);

  const seated = seatOrder.map((code) => ({
    code,
    seats: parties.find((party) => party.code === code)?.seats ?? 0,
  }));

  const notes = [
    [copy.source_label, copy.note_source],
    [copy.map_title, copy.note_districts],
    [
      copy.unseated,
      copy.note_unseated.replace("{count}", String(unseatedCount)),
    ],
    [copy.turnout_label, copy.note_turnout],
  ];
</script>

{#snippet monogram(size: number)}
  <svg
    class="tly-monogram"
    width={size}
    height={size}
    viewBox="0 0 32 32"
    aria-hidden="true"
  >
    <rect class="tly-mono-cell" x="1" y="1" width="30" height="30" rx="4" />
    <path class="tly-mono-marker" d="M5 12.6 L27 11.4 L27 19.4 L5 20.6 Z" />
    <path class="tly-mono-bars" d="M9 8 V24 M13 8 V24 M17 8 V24 M21 8 V24" />
    <path class="tly-mono-gate" d="M6 21.5 L26 12" />
  </svg>
{/snippet}

<div class="tly">
  <div class="tly-sheet">
    <a
      class="tly-cell tly-brand"
      href={resolve("/lab/[[direction=direction]]", { direction: "tally" })}
    >
      {@render monogram(28)}
      <span class="tly-wordmark">{copy.wordmark}</span>
    </a>
    <nav class="tly-cell tly-crumbs" aria-label={copy.breadcrumb}>
      <a href={resolve("/lab/[[direction=direction]]", { direction: "tally" })}
        >{copy.crumb_country}</a
      >
      <span aria-hidden="true">/</span>
      <a href={resolve("/lab/[[direction=direction]]", { direction: "tally" })}
        >{copy.crumb_election}</a
      >
      <span aria-hidden="true">/</span>
      <span aria-current="page">{copy.crumb_body}</span>
    </nav>
    <label class="tly-cell tly-search">
      <input type="search" placeholder={copy.search} />
      <kbd>⌘K</kbd>
    </label>

    <section class="tly-cell tly-title">
      <h1>{copy.title}</h1>
      <p>{copy.subtitle}</p>
      <dl class="tly-meta">
        <div>
          <dt>{copy.stage_label}</dt>
          <dd>{copy.stage_value}</dd>
        </div>
        <div>
          <dt>{copy.source_label}</dt>
          <dd>{copy.source_value}</dd>
        </div>
      </dl>
    </section>
    <dl class="tly-stats">
      {#each stats as [label, value] (label)}
        <div class="tly-cell tly-stat">
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      {/each}
    </dl>

    <section class="tly-cell tly-map">
      <h2>{copy.map_title}</h2>
      <ConstituencyMap {view} />
    </section>
    <section class="tly-cell tly-results">
      <h2>{copy.national_result}</h2>
      <ResultsTable {view} />
    </section>
    <section class="tly-cell tly-seats">
      <h2>{copy.hemicycle_title}</h2>
      <Hemicycle {view} rows={12} />
      <p class="tly-majority">{copy.hemicycle_majority}</p>
      <ul class="tly-legend">
        {#each seated as party (party.code)}
          <li
            class={{ active: view.party === party.code }}
            style:--party={partyColor(party.code)}
            onpointerenter={() => (view.party = party.code)}
            onpointerleave={() => (view.party = null)}
          >
            <span class="tly-swatch"></span>
            <span>{party.code}</span>
            <span class="tly-num">{party.seats}</span>
            <span class="tly-num tly-share"
              >{percent(party.seats / totalSeats)}</span
            >
          </li>
        {/each}
      </ul>
    </section>
    <section class="tly-cell tly-detail">
      <ConstituencyDetail {view} />
    </section>

    <dl class="tly-notes">
      {#each notes as [label, note] (label)}
        <div class="tly-cell">
          <dt>{label}</dt>
          <dd>{note}</dd>
        </div>
      {/each}
    </dl>

    <section class="tly-cell tly-specimen">
      <h2>{copy.specimen_title}</h2>
      <Specimen
        fonts="Piazzolla, Sofia Sans Semi Condensed, and Martian Mono"
      />
    </section>

    <section class="tly-cell tly-brand-section">
      <h2>{copy.brand_title}</h2>
      <div class="tly-brand-row">
        <span class="tly-wordmark tly-wordmark-large">{copy.wordmark}</span>
        <div class="tly-mono-sizes">
          {@render monogram(96)}
          {@render monogram(48)}
          {@render monogram(32)}
          {@render monogram(16)}
        </div>
        <div class="tly-mono-dark" title={copy.brand_on_dark}>
          {@render monogram(48)}
          <span class="tly-wordmark">{copy.wordmark}</span>
        </div>
      </div>
    </section>
  </div>
</div>

<style>
  .tly {
    padding: 14px;
  }

  .tly-sheet {
    display: grid;
    grid-template-columns: minmax(300px, 1fr) minmax(0, 1.5fr) minmax(
        300px,
        0.95fr
      );
    gap: 1px;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    overflow: hidden;
    background: var(--rule);
  }

  .tly-cell {
    min-inline-size: 0;
    padding: 12px 14px;
    background: var(--surface);
  }

  .tly h2 {
    margin-block-end: 10px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.01em;
  }

  .tly-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--ink);
    text-decoration: none;
  }

  .tly-wordmark {
    font-size: 21px;
    font-weight: 780;
    letter-spacing: -0.015em;
    white-space: nowrap;
  }

  .tly-mono-cell {
    fill: var(--surface);
    stroke: var(--ink);
    stroke-width: 1.5;
  }

  .tly-mono-marker {
    fill: var(--marker);
    opacity: 0.9;
  }

  .tly-mono-bars,
  .tly-mono-gate {
    fill: none;
    stroke: var(--ink);
    stroke-width: 2.2;
    stroke-linecap: round;
  }

  .tly-crumbs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 14px;
  }

  .tly-crumbs a {
    color: inherit;
    text-decoration: none;
  }

  .tly-crumbs a:hover {
    color: var(--ink);
    background: linear-gradient(transparent 55%, var(--hl) 55%);
  }

  .tly-crumbs [aria-current] {
    color: var(--ink);
    font-weight: 600;
  }

  .tly-search {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .tly-search:focus-within {
    box-shadow: inset 0 -3px 0 var(--marker);
  }

  .tly-search input {
    flex: 1;
    min-inline-size: 0;
    background: none;
    color: var(--ink);
    font: 14px/1.2 var(--font-ui);
    outline: none;
  }

  .tly-search kbd {
    padding: 1px 6px;
    border: 1px solid var(--rule);
    border-block-end-width: 2px;
    border-radius: var(--radius-sm);
    font: 11px/1.4 var(--font-num);
  }

  .tly-title {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
    gap: 6px 24px;
    padding-block: 16px;
  }

  .tly-title h1 {
    font-family: var(--font-display);
    font-size: clamp(28px, 3vw, 40px);
    font-weight: var(--display-weight);
    line-height: 1.02;
    letter-spacing: var(--display-tracking);
  }

  .tly-title p {
    grid-row: 2;
    color: var(--muted);
    font-size: 15px;
  }

  .tly-meta {
    grid-row: 1 / 3;
    grid-column: 2;
    display: grid;
    gap: 6px;
    font-size: 13.5px;
  }

  .tly-meta div {
    display: flex;
    gap: 8px;
  }

  .tly-meta dt {
    color: var(--muted);
  }

  .tly-meta dd {
    font-weight: 600;
  }

  .tly-stats {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 1px;
  }

  .tly-stat dt {
    color: var(--muted);
    font-size: 12.5px;
  }

  .tly-stat dd {
    margin-block-start: 2px;
    font-family: var(--font-num);
    font-size: 26px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.02em;
  }

  .tly-map {
    grid-column: 1;
    grid-row: 4 / 6;
    --map-max: 600px;
  }

  .tly-results {
    grid-column: 2;
    grid-row: 4;
    --table-size: 13px;
    --cell-y: 4px;
    --cell-x: 8px;
    --num-size: 0.92em;
  }

  .tly-seats {
    grid-column: 3;
    grid-row: 4;
  }

  .tly-detail {
    grid-column: 2 / 4;
    grid-row: 5;
  }

  .tly-majority {
    margin-block-start: 2px;
    font-size: 13px;
    text-align: center;
  }

  .tly-legend {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto auto;
    margin-block-start: 10px;
    border-block-start: 1px solid var(--rule);
    font-size: 13px;
  }

  .tly-legend li {
    display: grid;
    grid-template-columns: subgrid;
    grid-column: 1 / -1;
    align-items: center;
    gap: 8px;
    padding: 3px 4px;
    border-block-end: 1px solid
      color-mix(in oklab, var(--rule) 60%, transparent);
  }

  .tly-share {
    min-inline-size: 6ch;
    color: var(--muted);
    text-align: end;
  }

  .tly-legend li.active {
    background: linear-gradient(
      176deg,
      transparent 10%,
      var(--hl) 10% 90%,
      transparent 90%
    );
  }

  .tly-swatch {
    inline-size: 8px;
    block-size: 8px;
    background: var(--party);
  }

  .tly-num {
    font-family: var(--font-num);
    font-variant-numeric: tabular-nums;
    text-align: end;
  }

  .tly-notes {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 1px;
    font-size: 13px;
    line-height: 1.45;
  }

  .tly-notes dt {
    margin-block-end: 4px;
    font-weight: 700;
  }

  .tly-notes dd {
    color: var(--muted);
  }

  .tly-specimen,
  .tly-brand-section {
    grid-column: 1 / -1;
    padding: 18px;
  }

  .tly-brand-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 28px 44px;
  }

  .tly-wordmark-large {
    font-size: 46px;
  }

  .tly-mono-sizes {
    display: flex;
    align-items: end;
    gap: 18px;
  }

  .tly-mono-dark {
    display: inline-flex;
    align-items: center;
    gap: 14px;
    padding: 16px 22px;
    border-radius: var(--radius);
    background: var(--ink);
    color: var(--surface);
  }

  .tly-mono-dark .tly-mono-cell {
    fill: var(--ink);
    stroke: var(--surface);
  }

  .tly-mono-dark .tly-mono-bars,
  .tly-mono-dark .tly-mono-gate {
    stroke: var(--surface);
  }

  .tly :global(.rt th),
  .tly :global(.rt td) {
    border-inline-start: 1px solid
      color-mix(in oklab, var(--rule) 70%, transparent);
  }

  .tly :global(.rt th:first-child),
  .tly :global(.rt td:first-child) {
    border-inline-start: 0;
  }

  .tly :global(.rt thead th) {
    color: var(--ink);
    font-weight: 700;
    background: var(--surface-2);
  }

  .tly :global(.rt-row.active) {
    background: linear-gradient(
      178deg,
      transparent 6%,
      var(--hl) 6% 94%,
      transparent 94%
    );
  }

  .tly :global(.rt-threshold td) {
    color: var(--ink);
    font-weight: 600;
    border-block-end: 2px solid var(--marker);
  }

  .tly :global(.cmap-mode.active) {
    background: var(--marker);
    color: #191816;
    box-shadow: none;
  }

  @media (width < 1200px) {
    .tly-sheet {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .tly-search,
    .tly-results,
    .tly-detail {
      grid-column: 1 / -1;
      grid-row: auto;
    }

    .tly-map,
    .tly-seats {
      grid-column: auto;
      grid-row: auto;
    }

    .tly-stats {
      grid-template-columns: repeat(6, minmax(0, 1fr));
    }

    .tly-stat {
      grid-column: span 2;
    }

    .tly-stat:nth-child(n + 4) {
      grid-column: span 3;
    }
  }

  @media (width < 760px) {
    .tly {
      padding: 0;
    }

    .tly-sheet {
      grid-template-columns: minmax(0, 1fr);
      border-inline: 0;
      border-radius: 0;
    }

    .tly-sheet > * {
      grid-column: 1 / -1;
      grid-row: auto;
    }

    .tly-title {
      grid-template-columns: minmax(0, 1fr);
    }

    .tly-meta {
      grid-row: auto;
      grid-column: auto;
    }

    .tly-stats {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .tly-stat,
    .tly-stat:nth-child(n + 4) {
      grid-column: auto;
    }

    .tly-stat:last-child {
      grid-column: 1 / -1;
    }

    .tly-stat dd {
      font-size: 21px;
    }
  }
</style>
