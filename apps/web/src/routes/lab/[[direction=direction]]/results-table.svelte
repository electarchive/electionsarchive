<script lang="ts">
  import type { LabView } from "./view.svelte.js";
  import { copy } from "./copy.js";
  import {
    bswShortfall,
    count,
    otherParties,
    partyColor,
    percent,
    summary,
    tableParties,
    thresholdIndex,
  } from "./election.js";

  const { view, marks = false }: { view: LabView; marks?: boolean } = $props();

  const topShare = tableParties[0]?.secondShare ?? 1;
</script>

<div class="rt-wrap">
  <table class="rt">
    <thead>
      <tr>
        <th class="rt-party" scope="col">{copy.col_party}</th>
        <th class="rt-num" scope="col">{copy.col_second}</th>
        <th class="rt-num rt-share" scope="col">{copy.col_share}</th>
        <th class="rt-num rt-first" scope="col">{copy.col_first}</th>
        <th class="rt-num rt-first rt-share" scope="col">{copy.col_share}</th>
        <th class="rt-num" scope="col">
          <span class="rt-long">{copy.col_won}</span><abbr
            class="rt-short"
            title={copy.col_won}>{copy.col_won_short}</abbr
          >{#if marks}<sup class="mark">3</sup>{/if}
        </th>
        <th class="rt-num" scope="col">{copy.col_seats}</th>
      </tr>
    </thead>
    <tbody>
      {#each tableParties as party, index (party.code)}
        {#if index === thresholdIndex}
          <tr class="rt-threshold">
            <td colspan="7"><span>{copy.threshold}</span></td>
          </tr>
        {/if}
        <tr
          class={{ "rt-row": true, active: view.party === party.code }}
          style:--party={partyColor(party.code)}
          tabindex="0"
          onpointerenter={() => (view.party = party.code)}
          onpointerleave={() => (view.party = null)}
          onfocus={() => (view.party = party.code)}
          onblur={() => (view.party = null)}
        >
          <th class="rt-party" scope="row">
            <span class="rt-label">
              <span class="rt-swatch"></span>
              <span class="rt-code" title={party.name}>{party.code}</span>
              {#if marks && party.code === "SSW"}<sup class="mark">5</sup>{/if}
              {#if marks && party.code === "BSW"}<sup class="mark">6</sup>{/if}
              <span class="rt-name">{party.name}</span>
            </span>
          </th>
          <td class="rt-num">{count.format(party.second)}</td>
          <td class="rt-num rt-share">
            {percent(party.secondShare)}
            <span
              class="rt-bar"
              style:inline-size="{(party.secondShare / topShare) * 100}%"
            ></span>
          </td>
          <td class="rt-num rt-first">{count.format(party.first)}</td>
          <td class="rt-num rt-first rt-share">{percent(party.firstShare)}</td>
          <td class="rt-num">
            {party.won || ""}
            {#if party.unseated > 0}
              <span class="rt-unseated"
                >{copy.not_seated.replace(
                  "{count}",
                  String(party.unseated),
                )}</span
              >
            {/if}
          </td>
          <td class="rt-num rt-seats">{party.seats || ""}</td>
        </tr>
      {/each}
      <tr class="rt-row rt-others" style:--party="var(--p-other)">
        <th class="rt-party" scope="row">
          <span class="rt-label">
            <span class="rt-swatch"></span>
            <span class="rt-code">{copy.others}</span>
          </span>
        </th>
        <td class="rt-num">{count.format(otherParties.second)}</td>
        <td class="rt-num rt-share"
          >{percent(otherParties.second / summary.valid_second)}</td
        >
        <td class="rt-num rt-first">{count.format(otherParties.first)}</td>
        <td class="rt-num rt-first rt-share"
          >{percent(otherParties.first / summary.valid_first)}</td
        >
        <td class="rt-num">{otherParties.won || ""}</td>
        <td class="rt-num rt-seats"></td>
      </tr>
    </tbody>
  </table>
  {#if !marks}
    <p class="rt-note">{copy.ssw_note}</p>
    <p class="rt-note">
      {copy.bsw_note.replace("{votes}", count.format(bswShortfall))}
    </p>
  {/if}
</div>

<style>
  .rt-wrap {
    min-inline-size: 0;
    overflow-x: auto;
    container-type: inline-size;
  }

  .rt {
    inline-size: 100%;
    border-collapse: collapse;
    font-family: var(--font-ui);
    font-size: var(--table-size, 13px);
    line-height: 1.25;
    color: var(--ink);
  }

  .rt th,
  .rt td {
    padding: var(--cell-y, 6px) var(--cell-x, 8px);
    text-align: start;
    vertical-align: baseline;
  }

  .rt thead th {
    color: var(--muted);
    font-weight: 500;
    font-size: 0.92em;
    line-height: 1.2;
    vertical-align: bottom;
    white-space: normal;
    text-wrap: balance;
    border-block-end: 1px solid var(--rule);
  }

  .rt-num {
    text-align: end !important;
    font-family: var(--font-num);
    font-variant-numeric: tabular-nums lining-nums;
    font-size: var(--num-size, 0.95em);
    white-space: nowrap;
  }

  thead .rt-num {
    font-family: var(--font-ui);
  }

  .rt-row {
    border-block-end: 1px solid
      color-mix(in oklab, var(--rule) 60%, transparent);
    transition: background-color 120ms ease;
  }

  .rt-row:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }

  .rt-row.active {
    background: var(--hl);
  }

  .rt-party {
    font-weight: 400;
  }

  .rt-label {
    display: flex;
    align-items: baseline;
    gap: 7px;
    min-inline-size: 0;
  }

  .rt-swatch {
    flex: none;
    align-self: center;
    inline-size: 9px;
    block-size: 9px;
    border-radius: 2px;
    background: var(--party);
  }

  .rt-code {
    font-weight: 600;
    white-space: nowrap;
  }

  .rt-name {
    overflow: hidden;
    max-inline-size: 17ch;
    color: var(--muted);
    font-size: 0.9em;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .rt-share {
    position: relative;
    color: var(--muted);
  }

  .rt-bar {
    position: absolute;
    inset-block-end: 1px;
    inset-inline-end: var(--cell-x, 8px);
    block-size: 2px;
    max-inline-size: calc(100% - 2 * var(--cell-x, 8px));
    background: var(--party);
  }

  .rt-unseated {
    margin-inline-start: 4px;
    color: var(--muted);
    font-family: var(--font-ui);
    font-size: 0.82em;
  }

  .rt-seats {
    font-weight: 600;
  }

  .rt-threshold td {
    padding-block: 8px 4px;
    color: var(--accent);
    font-size: 0.86em;
    border-block-end: 1px dashed var(--accent);
  }

  .rt-others {
    color: var(--muted);
  }

  .rt-note {
    margin-block-start: 8px;
    color: var(--muted);
    font: 12px/1.45 var(--font-ui);
  }

  @container (width < 520px) {
    .rt-first {
      display: none;
    }
  }

  .rt-short {
    display: none;
    text-decoration: none;
  }

  @container (width < 420px) {
    .rt {
      --cell-x: 4px;
    }

    .rt-long {
      display: none;
    }

    .rt-short {
      display: inline;
    }

    .rt-code {
      display: block;
      max-inline-size: 6.5em;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .rt-unseated {
      display: block;
      margin-inline-start: 0;
      line-height: 1.2;
    }
  }

  @container (width < 720px) {
    .rt-name {
      display: none;
    }
  }

  .mark {
    margin-inline-start: 1px;
    color: var(--accent);
    font-family: var(--font-display);
    font-size: 0.72em;
    font-weight: 600;
  }
</style>
