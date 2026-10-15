<script lang="ts">
  import { resolve } from "$app/paths";
  import type { LabView } from "./view.svelte.js";
  import ConstituencyDetail from "./constituency-detail.svelte";
  import ConstituencyMap from "./constituency-map.svelte";
  import { copy } from "./copy.js";
  import {
    bswShortfall,
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

  const { view }: { view: LabView } = $props();

  const notes = [
    copy.note_source,
    copy.note_districts,
    copy.note_unseated.replace("{count}", String(unseatedCount)),
    copy.note_turnout,
    copy.ssw_note,
    copy.bsw_note.replace("{votes}", count.format(bswShortfall)),
  ];

  const seated = seatOrder.map((code) => ({
    code,
    seats: parties.find((party) => party.code === code)?.seats ?? 0,
  }));
</script>

{#snippet monogram(size: number)}
  <svg
    class="ind-monogram"
    width={size}
    height={size}
    viewBox="0 0 32 32"
    aria-hidden="true"
  >
    <rect class="ind-mono-tile" x="0.5" y="0.5" width="31" height="31" rx="8" />
    <rect class="ind-mono-box" x="8" y="8" width="16" height="16" rx="1.5" />
    <path class="ind-mono-mark" d="M10.2 9.6 L23.4 23.2 M23.9 9.1 L9.4 23.8" />
  </svg>
{/snippet}

<div class="ind">
  <header class="ind-head">
    <a
      class="ind-brand"
      href={resolve("/lab/[[direction=direction]]", { direction: "indelible" })}
    >
      {@render monogram(30)}
      <span class="ind-wordmark">{copy.wordmark}</span>
    </a>
    <nav class="ind-crumbs" aria-label={copy.breadcrumb}>
      <a
        href={resolve("/lab/[[direction=direction]]", {
          direction: "indelible",
        })}>{copy.crumb_country}</a
      >
      <span aria-hidden="true">/</span>
      <a
        href={resolve("/lab/[[direction=direction]]", {
          direction: "indelible",
        })}>{copy.crumb_election}</a
      >
      <span aria-hidden="true">/</span>
      <span aria-current="page">{copy.crumb_body}</span>
    </nav>
    <label class="ind-search">
      <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
        <circle
          cx="7"
          cy="7"
          r="4.6"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
        />
        <path
          d="M10.4 10.4 14 14"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linecap="round"
        />
      </svg>
      <input type="search" placeholder={copy.search} />
      <kbd>⌘K</kbd>
    </label>
  </header>

  <div class="ind-body">
    <main class="ind-main">
      <section class="ind-title">
        <h1>{copy.title}<sup class="mark">1</sup></h1>
        <p class="ind-sub">{copy.subtitle}</p>
        <dl class="ind-fields">
          <div>
            <dt>{copy.stage_label}</dt>
            <dd class="ind-text">{copy.stage_value}</dd>
          </div>
          <div>
            <dt>{copy.source_label}</dt>
            <dd class="ind-text">{copy.source_value}</dd>
          </div>
          <div>
            <dt>{copy.seats_label}</dt>
            <dd>{totalSeats}</dd>
          </div>
          <div>
            <dt>{copy.majority_label}</dt>
            <dd>{majority}</dd>
          </div>
          <div>
            <dt>{copy.turnout_label}</dt>
            <dd>{percent(turnout)}<sup class="mark">4</sup></dd>
          </div>
          <div>
            <dt>{copy.registered_label}</dt>
            <dd>{count.format(summary.registered)}</dd>
          </div>
        </dl>
      </section>

      <div class="ind-grid">
        <section class="ind-panel ind-map">
          <h2>{copy.map_title}<sup class="mark">2</sup></h2>
          <ConstituencyMap {view} />
        </section>
        <section class="ind-panel ind-results">
          <h2>{copy.national_result}</h2>
          <ResultsTable {view} marks />
        </section>
        <section class="ind-panel ind-seats">
          <h2>{copy.hemicycle_title}</h2>
          <Hemicycle {view} />
          <p class="ind-majority">{copy.hemicycle_majority}</p>
          <ul class="ind-legend">
            {#each seated as party (party.code)}
              <li
                class={{ active: view.party === party.code }}
                style:--party={partyColor(party.code)}
                onpointerenter={() => (view.party = party.code)}
                onpointerleave={() => (view.party = null)}
              >
                <span class="ind-dot"></span>{party.code}
                <span class="ind-num">{party.seats}</span>
              </li>
            {/each}
          </ul>
        </section>
        <section class="ind-panel ind-detail">
          <ConstituencyDetail {view} />
        </section>
      </div>
    </main>

    <aside class="ind-notes">
      <h2>{copy.notes_title}</h2>
      <ol>
        {#each notes as note, index (index)}
          <li><span class="ind-note-number">{index + 1}</span>{note}</li>
        {/each}
      </ol>
    </aside>
  </div>

  <section class="ind-specimen">
    <h2>{copy.specimen_title}</h2>
    <Specimen fonts="Literata, IBM Plex Sans, and IBM Plex Mono" />
  </section>

  <section class="ind-brand-section">
    <h2>{copy.brand_title}</h2>
    <div class="ind-brand-row">
      <span class="ind-wordmark ind-wordmark-large">{copy.wordmark}</span>
      <div class="ind-mono-sizes">
        {@render monogram(96)}
        {@render monogram(48)}
        {@render monogram(32)}
        {@render monogram(16)}
      </div>
      <div class="ind-mono-dark" title={copy.brand_on_dark}>
        {@render monogram(48)}
        <span class="ind-wordmark">{copy.wordmark}</span>
      </div>
    </div>
  </section>
</div>

<style>
  .ind {
    max-inline-size: 1480px;
    margin-inline: auto;
    padding: 18px 28px 72px;
  }

  .ind h2 {
    margin-block-end: 10px;
    font-family: var(--font-display);
    font-size: 19px;
    font-weight: 560;
    letter-spacing: -0.01em;
  }

  .ind-head {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) minmax(240px, 380px);
    align-items: center;
    gap: 12px 28px;
    padding-block-end: 14px;
    border-block-end: 1px solid var(--rule);
  }

  .ind-brand {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    color: var(--ink);
    text-decoration: none;
  }

  .ind-wordmark {
    font-family: var(--font-display);
    font-size: 21px;
    font-weight: 600;
    letter-spacing: -0.025em;
    white-space: nowrap;
  }

  .ind-mono-tile {
    fill: var(--ink);
  }

  .ind-mono-box {
    fill: none;
    stroke: var(--surface);
    stroke-width: 1.6;
  }

  .ind-mono-mark {
    fill: none;
    stroke: var(--accent);
    stroke-width: 3.4;
    stroke-linecap: round;
  }

  .ind-crumbs {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    color: var(--muted);
    font-size: 13.5px;
  }

  .ind-crumbs a {
    color: inherit;
    text-decoration: none;
  }

  .ind-crumbs a:hover {
    color: var(--ink);
    text-decoration: underline;
    text-decoration-color: var(--accent);
    text-underline-offset: 3px;
  }

  .ind-crumbs [aria-current] {
    color: var(--ink);
  }

  .ind-search {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px 6px 10px;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--muted);
  }

  .ind-search:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--hl);
  }

  .ind-search input {
    flex: 1;
    min-inline-size: 0;
    background: none;
    color: var(--ink);
    font: 13.5px/1.2 var(--font-ui);
    outline: none;
  }

  .ind-search kbd {
    padding: 1px 6px;
    border: 1px solid var(--rule);
    border-radius: var(--radius-sm);
    font: 11.5px/1.4 var(--font-num);
  }

  .ind-body {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 248px;
    gap: 36px;
    padding-block-start: 26px;
  }

  .ind-title h1 {
    max-inline-size: 18ch;
    font-family: var(--font-display);
    font-size: clamp(34px, 4.4vw, 60px);
    font-weight: var(--display-weight);
    line-height: 1.02;
    letter-spacing: var(--display-tracking);
    text-wrap: balance;
  }

  .ind-sub {
    margin-block-start: 8px;
    color: var(--muted);
    font-family: var(--font-display);
    font-size: 19px;
    font-style: italic;
  }

  .ind-fields {
    display: flex;
    flex-wrap: wrap;
    gap: 14px 34px;
    margin-block: 22px 26px;
  }

  .ind-fields dt {
    color: var(--muted);
    font-size: 12px;
  }

  .ind-fields dd {
    margin-block-start: 2px;
    font-family: var(--font-num);
    font-size: 16px;
    font-variant-numeric: tabular-nums;
  }

  .ind-fields dd.ind-text {
    font-family: var(--font-ui);
    font-size: 15px;
    font-weight: 500;
  }

  .ind-grid {
    display: grid;
    grid-template-columns: minmax(280px, 4fr) minmax(0, 7fr);
    gap: 16px;
    align-items: start;
  }

  .ind-panel {
    min-inline-size: 0;
    padding: 16px 18px 18px;
    border: 1px solid var(--rule);
    border-radius: var(--radius-lg);
    background: var(--surface);
  }

  .ind-map {
    --map-max: 620px;
  }

  .ind-results {
    --table-size: 13.5px;
    --cell-x: 7px;
    --cell-y: 6px;

    font-stretch: 87%;
  }

  .ind-majority {
    margin-block-start: 4px;
    color: var(--accent);
    font-size: 12.5px;
    text-align: center;
  }

  .ind-legend {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 4px 14px;
    margin-block-start: 10px;
    font-size: 13px;
  }

  .ind-legend li {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 1px 4px;
    border-radius: var(--radius-sm);
    cursor: default;
  }

  .ind-legend li.active {
    background: var(--hl);
  }

  .ind-dot {
    inline-size: 8px;
    block-size: 8px;
    border-radius: 50%;
    background: var(--party);
  }

  .ind-num {
    font-family: var(--font-num);
    font-variant-numeric: tabular-nums;
  }

  .ind-notes {
    position: sticky;
    inset-block-start: 60px;
    align-self: start;
    padding-inline-start: 18px;
    border-inline-start: 1px solid var(--rule);
  }

  .ind-notes ol {
    display: grid;
    gap: 14px;
    font-size: 13px;
    line-height: 1.5;
  }

  .ind-notes li {
    display: grid;
    grid-template-columns: 18px 1fr;
  }

  .ind-note-number {
    color: var(--accent);
    font-family: var(--font-display);
    font-weight: 600;
  }

  .ind-specimen,
  .ind-brand-section {
    margin-block-start: 44px;
    padding-block-start: 22px;
    border-block-start: 1px solid var(--rule);
  }

  .ind-brand-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 28px 44px;
  }

  .ind-wordmark-large {
    font-size: 44px;
  }

  .ind-mono-sizes {
    display: flex;
    align-items: end;
    gap: 18px;
  }

  .ind-mono-dark {
    display: inline-flex;
    align-items: center;
    gap: 14px;
    padding: 16px 22px;
    border-radius: var(--radius-lg);
    background: var(--ink);
    color: var(--surface);
  }

  .ind-mono-dark .ind-mono-tile {
    fill: var(--surface);
  }

  .ind-mono-dark .ind-mono-box {
    stroke: var(--ink);
  }

  .ind :global(.mark) {
    margin-inline-start: 2px;
    color: var(--accent);
    font-family: var(--font-display);
    font-size: 0.5em;
    font-weight: 600;
    vertical-align: super;
    line-height: 0;
  }

  .ind-title :global(.mark) {
    font-size: 0.38em;
  }

  .ind-fields :global(.mark) {
    font-size: 0.7em;
  }

  @media (width < 1100px) {
    .ind-body {
      grid-template-columns: minmax(0, 1fr);
    }

    .ind-notes {
      position: static;
      padding-inline-start: 0;
      border-inline-start: 0;
    }
  }

  @media (width < 860px) {
    .ind {
      padding-inline: 16px;
    }

    .ind-head {
      grid-template-columns: minmax(0, 1fr);
    }

    .ind-grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }

  @media (width < 520px) {
    .ind-panel {
      padding-inline: 12px;
    }

    .ind-results {
      --table-size: 12.5px;
    }
  }
</style>
