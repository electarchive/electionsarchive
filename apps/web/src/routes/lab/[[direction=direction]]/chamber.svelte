<script lang="ts">
  import { resolve } from "$app/paths";
  import type { LabView } from "./view.svelte.js";
  import ConstituencyDetail from "./constituency-detail.svelte";
  import ConstituencyMap from "./constituency-map.svelte";
  import { copy } from "./copy.js";
  import {
    count,
    lead,
    parties,
    partyColor,
    percent,
    seatOrder,
    summary,
    turnout,
    unseatedCount,
  } from "./election.js";
  import Hemicycle from "./hemicycle.svelte";
  import ResultsTable from "./results-table.svelte";
  import Specimen from "./specimen.svelte";

  const { view, motion }: { view: LabView; motion: boolean } = $props();

  const reflect = $derived.by(() => {
    if (view.party != null) return partyColor(view.party);
    if (
      view.hovered != null &&
      view.focused != null &&
      view.mode !== "turnout"
    ) {
      return partyColor(lead(view.focused, view.mode).party);
    }
    return null;
  });

  const seated = seatOrder.map((code) => ({
    code,
    seats: parties.find((party) => party.code === code)?.seats ?? 0,
  }));

  const dots: Array<{ x: number; y: number; angle: number }> = [];
  for (const [radius, seats] of [
    [5, 5],
    [8.5, 8],
    [12, 11],
  ] as const) {
    for (let seat = 0; seat < seats; seat++) {
      const angle = Math.PI - (seat * Math.PI) / (seats - 1);
      dots.push({
        x: 16 + radius * Math.cos(angle),
        y: 22 - radius * Math.sin(angle),
        angle,
      });
    }
  }
  dots.sort((a, b) => b.angle - a.angle);

  const notes = [
    copy.note_source,
    copy.note_districts,
    copy.note_unseated.replace("{count}", String(unseatedCount)),
    copy.note_turnout,
  ];
</script>

{#snippet monogram(size: number)}
  <svg
    class="chm-monogram"
    width={size}
    height={size}
    viewBox="0 0 32 32"
    aria-hidden="true"
  >
    <rect class="chm-mono-tile" width="32" height="32" rx="9" />
    {#each dots as dot, index (index)}
      <circle
        class={{ "chm-mono-dot": true, minority: index >= 13 }}
        cx={dot.x}
        cy={dot.y}
        r="1.2"
      />
    {/each}
  </svg>
{/snippet}

<div class="chm" style:--reflect={reflect}>
  <section class="chm-scene">
    <div class="chm-map">
      <ConstituencyMap {view} />
    </div>
    <div class="chm-blur" aria-hidden="true">
      <span></span>
      <span></span>
      <span></span>
      <span></span>
    </div>

    <header class="chm-header chm-glass">
      <a
        class="chm-brand"
        href={resolve("/lab/[[direction=direction]]", { direction: "chamber" })}
      >
        {@render monogram(26)}
        <span class="chm-wordmark">{copy.wordmark}</span>
      </a>
      <nav class="chm-crumbs" aria-label={copy.breadcrumb}>
        <a
          href={resolve("/lab/[[direction=direction]]", {
            direction: "chamber",
          })}>{copy.crumb_country}</a
        >
        <span aria-hidden="true">/</span>
        <a
          href={resolve("/lab/[[direction=direction]]", {
            direction: "chamber",
          })}>{copy.crumb_election}</a
        >
        <span aria-hidden="true">/</span>
        <span aria-current="page">{copy.crumb_body}</span>
      </nav>
      <label class="chm-search">
        <input type="search" placeholder={copy.search} />
        <kbd>⌘K</kbd>
      </label>
    </header>

    <aside class="chm-column chm-glass">
      <div class="chm-title">
        <h1>{copy.title}</h1>
        <p>{copy.subtitle}</p>
      </div>
      <div class="chm-hero">
        <Hemicycle {view} center animate={motion} rows={14} />
        <p class="chm-majority">{copy.hemicycle_majority}</p>
        <ul class="chm-legend" aria-label={copy.hemicycle_title}>
          {#each seated as party (party.code)}
            <li
              class={{ active: view.party === party.code }}
              style:--party={partyColor(party.code)}
              onpointerenter={() => (view.party = party.code)}
              onpointerleave={() => (view.party = null)}
            >
              <span class="chm-swatch"></span>
              {party.code}
              <span class="chm-num">{party.seats}</span>
            </li>
          {/each}
        </ul>
      </div>
      <dl class="chm-facts">
        <div>
          <dt>{copy.stage_label}</dt>
          <dd>{copy.stage_value}</dd>
        </div>
        <div>
          <dt>{copy.source_label}</dt>
          <dd>{copy.source_value}</dd>
        </div>
        <div>
          <dt>{copy.turnout_label}</dt>
          <dd class="chm-num">{percent(turnout)}</dd>
        </div>
        <div>
          <dt>{copy.registered_label}</dt>
          <dd class="chm-num">{count.format(summary.registered)}</dd>
        </div>
      </dl>
      <section class="chm-results">
        <h2>{copy.national_result}</h2>
        <ResultsTable {view} />
      </section>
      <details class="chm-notes">
        <summary>{copy.notes_title}</summary>
        <ol>
          {#each notes as note (note)}
            <li>{note}</li>
          {/each}
        </ol>
      </details>
    </aside>

    <section class="chm-sheet chm-glass" aria-label={copy.map_title}>
      <ConstituencyDetail {view} />
    </section>
  </section>

  <div class="chm-below">
    <section class="chm-panel">
      <h2>{copy.specimen_title}</h2>
      <Specimen fonts="Source Serif 4, Inter, and JetBrains Mono" />
    </section>
    <section class="chm-panel">
      <h2>{copy.brand_title}</h2>
      <div class="chm-brand-row">
        <span class="chm-wordmark chm-wordmark-large">{copy.wordmark}</span>
        <div class="chm-mono-sizes">
          {@render monogram(96)}
          {@render monogram(48)}
          {@render monogram(32)}
          {@render monogram(16)}
        </div>
        <div class="chm-mono-dark" title={copy.brand_on_dark}>
          {@render monogram(48)}
          <span class="chm-wordmark">{copy.wordmark}</span>
        </div>
      </div>
    </section>
  </div>
</div>

<style>
  .chm {
    transition: --reflect 320ms ease;
  }

  .chm-scene {
    position: relative;
    block-size: max(760px, calc(100dvh - 46px));
    overflow: hidden;
    background:
      repeating-radial-gradient(
        circle at 50% 118%,
        transparent 0 47px,
        color-mix(in oklab, var(--ink) 7%, transparent) 47px 48px
      ),
      radial-gradient(
        ellipse at 58% 55%,
        color-mix(in oklab, var(--surface) 70%, transparent),
        transparent 65%
      ),
      var(--ground);
  }

  .chm h2 {
    margin-block-end: 8px;
    font-size: 13px;
    font-weight: 600;
  }

  .chm-glass {
    border: 1px solid var(--rule);
    border-radius: var(--radius-lg);
    background: var(--surface);
    box-shadow: 0 1px 2px color-mix(in oklab, var(--ink) 10%, transparent);
  }

  :global(.lab[data-glass]) .chm-glass {
    border-color: var(--glass-edge);
    background: var(--glass);
    backdrop-filter: blur(22px) saturate(1.6);
  }

  :global(.lab[data-shadows]) .chm-glass {
    box-shadow:
      inset 0 1px 0 var(--glass-hi),
      0 18px 50px -16px color-mix(in oklab, var(--reflect) 60%, transparent),
      0 2px 10px -2px color-mix(in oklab, var(--reflect) 28%, transparent);
  }

  .chm-map :global(.cmap) {
    display: contents;
  }

  .chm-map :global(.cmap-svg) {
    position: absolute;
    inset-block-start: 84px;
    inset-inline-start: 480px;
    inline-size: calc(100% - 480px - 412px);
    block-size: calc(100% - 96px);
    max-block-size: none;
  }

  .chm-map :global(.cmap-bar) {
    position: absolute;
    inset-block-start: 84px;
    inset-inline-end: 16px;
    z-index: 2;
    justify-content: end;
  }

  .chm-map :global(.cmap-modes),
  .chm-map :global(.cmap-select) {
    border-color: var(--glass-edge);
    border-radius: var(--radius);
    background: var(--glass);
    backdrop-filter: blur(16px) saturate(1.4);
  }

  .chm-map :global(.cmap-mode) {
    border-radius: calc(var(--radius) - 2px);
  }

  .chm-map :global(.cmap-mode.active) {
    background: var(--accent);
    color: var(--accent-ink);
    box-shadow: none;
  }

  .chm-map :global(.cmap-legend) {
    position: absolute;
    inset-block-start: 124px;
    inset-inline-end: 18px;
    z-index: 2;
  }

  .chm-blur {
    display: none;
    position: absolute;
    inset: 0 0 auto;
    z-index: 1;
    block-size: 112px;
    pointer-events: none;
    background: linear-gradient(
      to bottom,
      color-mix(in oklab, var(--ground) 55%, transparent),
      transparent
    );
  }

  :global(.lab[data-blur]) .chm-blur {
    display: block;
  }

  .chm-blur span {
    position: absolute;
    inset: 0;
  }

  .chm-blur span:nth-child(1) {
    backdrop-filter: blur(1px);
    mask: linear-gradient(to bottom, black 55%, transparent 100%);
  }

  .chm-blur span:nth-child(2) {
    backdrop-filter: blur(3px);
    mask: linear-gradient(to bottom, black 38%, transparent 75%);
  }

  .chm-blur span:nth-child(3) {
    backdrop-filter: blur(7px);
    mask: linear-gradient(to bottom, black 22%, transparent 52%);
  }

  .chm-blur span:nth-child(4) {
    backdrop-filter: blur(14px);
    mask: linear-gradient(to bottom, black 8%, transparent 32%);
  }

  .chm-header {
    position: absolute;
    inset: 14px 16px auto;
    z-index: 3;
    display: flex;
    align-items: center;
    gap: 12px 28px;
    padding: 8px 10px 8px 12px;
    border-radius: var(--radius-lg);
  }

  .chm-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--ink);
    text-decoration: none;
  }

  .chm-wordmark {
    font-family: var(--font-display);
    font-size: 19px;
    font-weight: 600;
    letter-spacing: -0.012em;
    white-space: nowrap;
  }

  .chm-mono-tile {
    fill: var(--accent);
  }

  .chm-mono-dot {
    fill: var(--accent-ink);
  }

  .chm-mono-dot.minority {
    opacity: 0.42;
  }

  .chm-crumbs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 13.5px;
  }

  .chm-crumbs a {
    color: inherit;
    text-decoration: none;
  }

  .chm-crumbs a:hover,
  .chm-crumbs [aria-current] {
    color: var(--ink);
  }

  .chm-search {
    display: flex;
    flex: 0 1 380px;
    align-items: center;
    gap: 8px;
    margin-inline-start: auto;
    padding: 6px 8px 6px 12px;
    border: 1px solid color-mix(in oklab, var(--ink) 12%, transparent);
    border-radius: var(--radius);
    background: color-mix(in oklab, var(--surface) 55%, transparent);
  }

  .chm-search:focus-within {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }

  .chm-search input {
    flex: 1;
    min-inline-size: 0;
    background: none;
    color: var(--ink);
    font: 13.5px/1.2 var(--font-ui);
    outline: none;
  }

  .chm-search kbd {
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    background: color-mix(in oklab, var(--ink) 8%, transparent);
    color: var(--muted);
    font: 11px/1.4 var(--font-num);
  }

  .chm-column {
    position: absolute;
    inset: 84px auto 16px 16px;
    z-index: 2;
    display: grid;
    align-content: start;
    gap: 18px;
    inline-size: 448px;
    padding: 20px 18px;
    overflow: auto;
    overscroll-behavior: contain;
    scrollbar-width: thin;
    --table-size: 12.5px;
    --cell-x: 6px;
    --cell-y: 4px;
    --num-size: 0.94em;
  }

  .chm-title h1 {
    font-family: var(--font-display);
    font-size: 30px;
    font-weight: var(--display-weight);
    font-optical-sizing: auto;
    line-height: 1.06;
    letter-spacing: var(--display-tracking);
    text-wrap: balance;
  }

  .chm-title p {
    margin-block-start: 6px;
    color: var(--muted);
    font-size: 14px;
  }

  .chm-hero {
    display: grid;
    gap: 6px;
    padding: 14px 12px 12px;
    border-radius: var(--radius);
    background: color-mix(in oklab, var(--surface) 62%, transparent);
  }

  .chm-majority {
    color: var(--muted);
    font-size: 12.5px;
    text-align: center;
  }

  .chm-legend {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 4px;
    margin-block-start: 4px;
    font-size: 12.5px;
  }

  .chm-legend li {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 8px 2px 6px;
    border-radius: 999px;
    background: color-mix(in oklab, var(--ink) 5%, transparent);
    cursor: default;
    transition: background 160ms ease;
  }

  .chm-legend li.active {
    background: color-mix(in oklab, var(--party) 22%, transparent);
  }

  .chm-swatch {
    inline-size: 8px;
    block-size: 8px;
    border-radius: 50%;
    background: var(--party);
  }

  .chm-num {
    font-family: var(--font-num);
    font-variant-numeric: tabular-nums;
  }

  .chm-legend .chm-num {
    color: var(--muted);
  }

  .chm-facts {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px 16px;
    font-size: 13px;
  }

  .chm-facts dt {
    color: var(--muted);
    font-size: 12px;
  }

  .chm-facts dd {
    font-weight: 550;
  }

  .chm-notes {
    font-size: 12.5px;
    line-height: 1.45;
  }

  .chm-notes summary {
    color: var(--muted);
    cursor: pointer;
  }

  .chm-notes summary:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .chm-notes ol {
    display: grid;
    gap: 6px;
    margin-block-start: 8px;
    padding-inline-start: 18px;
    list-style: decimal;
  }

  .chm-sheet {
    position: absolute;
    inset: auto 16px 16px auto;
    z-index: 2;
    inline-size: 380px;
    max-block-size: calc(100% - 180px);
    padding: 16px 18px;
    overflow: auto;
    scrollbar-width: thin;
  }

  .chm-below {
    display: grid;
    gap: 16px;
    padding: 16px;
  }

  .chm-panel {
    padding: 22px;
    border: 1px solid var(--rule);
    border-radius: var(--radius-lg);
    background: var(--surface);
  }

  .chm-brand-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 28px 44px;
  }

  .chm-wordmark-large {
    font-size: 48px;
  }

  .chm-mono-sizes {
    display: flex;
    align-items: end;
    gap: 18px;
  }

  .chm-mono-dark {
    --accent: #63d2be;
    --accent-ink: #13221f;

    display: inline-flex;
    align-items: center;
    gap: 14px;
    padding: 16px 22px;
    border-radius: var(--radius-lg);
    background: #1c1b19;
    color: #edeae3;
  }

  @media (width < 1180px) {
    .chm-scene {
      display: grid;
      gap: 12px;
      block-size: auto;
      padding: 12px;
    }

    .chm-header,
    .chm-column,
    .chm-sheet {
      position: static;
      inline-size: auto;
      max-block-size: none;
      overflow: visible;
    }

    .chm-header {
      flex-wrap: wrap;
    }

    .chm-search {
      flex-basis: 100%;
    }

    .chm-map {
      display: grid;
      order: 2;
    }

    .chm-header {
      order: 0;
    }

    .chm-column {
      order: 1;
    }

    .chm-sheet {
      order: 3;
    }

    .chm-map :global(.cmap) {
      display: grid;
      gap: 10px;
    }

    .chm-map :global(.cmap-svg),
    .chm-map :global(.cmap-bar),
    .chm-map :global(.cmap-legend) {
      position: static;
      inline-size: 100%;
      block-size: auto;
    }

    .chm-map :global(.cmap-svg) {
      max-block-size: 72dvh;
    }

    .chm-map :global(.cmap-bar) {
      justify-content: space-between;
    }

    :global(.lab[data-blur]) .chm-blur {
      display: none;
    }
  }

  @media (width < 640px) {
    .chm-column,
    .chm-sheet,
    .chm-panel {
      padding: 14px 12px;
    }

    .chm-crumbs {
      display: none;
    }

    .chm-wordmark-large {
      font-size: 34px;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .chm {
      transition: none;
    }
  }
</style>
