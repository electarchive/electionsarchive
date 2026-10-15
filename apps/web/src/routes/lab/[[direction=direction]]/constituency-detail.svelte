<script lang="ts">
  import type { LabView } from "./view.svelte.js";
  import { copy } from "./copy.js";
  import {
    constituencyTurnout,
    count,
    laender,
    partyColor,
    percent,
  } from "./election.js";

  const { view }: { view: LabView } = $props();

  const votes = $derived(
    view.focused == null
      ? []
      : ([
          [copy.first_votes, view.focused.first, view.focused.valid_first],
          [copy.second_votes, view.focused.second, view.focused.valid_second],
        ] as const),
  );
</script>

<div class="cd" aria-live="polite">
  {#if view.focused == null}
    <p class="cd-empty">{copy.constituency_empty}</p>
  {:else}
    <div class="cd-head">
      <span class="cd-code"
        >{copy.constituency.replace("{code}", view.focused.code)}</span
      >
      <span class="cd-name">{view.focused.name}</span>
      <span class="cd-land">{laender[view.focused.land]}</span>
    </div>
    <div class="cd-status" style:--party={partyColor(view.focused.winner)}>
      <span class="cd-chip">{view.focused.winner}</span>
      <span>{view.focused.seated ? copy.elected : copy.unseated}</span>
      {#if view.focused.member != null}<span class="cd-member"
          >{view.focused.member}</span
        >{/if}
      <span class="cd-turnout">
        {copy.turnout_label}
        <span class="cd-num">{percent(constituencyTurnout(view.focused))}</span>
      </span>
    </div>
    <div class="cd-columns">
      {#each votes as [label, rows, valid] (label)}
        <div class="cd-column">
          <span class="cd-label">{label}</span>
          {#each rows.slice(0, 6) as [party, value] (party)}
            <div
              class="cd-row"
              style:--party={partyColor(party === "Others" ? null : party)}
            >
              <span class="cd-party"
                >{party === "Others" ? copy.others : party}</span
              >
              <span class="cd-track"
                ><span
                  class="cd-bar"
                  style:inline-size="{(value / valid) * 100}%"
                ></span></span
              >
              <span class="cd-num">{percent(value / valid)}</span>
              <span class="cd-num cd-votes">{count.format(value)}</span>
            </div>
          {/each}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .cd {
    display: grid;
    gap: 10px;
    min-inline-size: 0;
    font-family: var(--font-ui);
    color: var(--ink);
  }

  .cd-empty {
    color: var(--muted);
    font-size: 13px;
  }

  .cd-head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 10px;
  }

  .cd-code {
    color: var(--muted);
    font-size: 12px;
  }

  .cd-name {
    font-family: var(--font-display);
    font-size: 19px;
    font-weight: 600;
    line-height: 1.15;
  }

  .cd-land {
    color: var(--muted);
    font-size: 12.5px;
  }

  .cd-status {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 10px;
    font-size: 13px;
  }

  .cd-chip {
    padding: 1px 7px;
    border: 1px solid var(--party);
    border-radius: var(--radius-sm);
    background: color-mix(in oklab, var(--party) 16%, transparent);
    color: var(--ink);
    font-weight: 600;
    font-size: 12px;
  }

  .cd-member {
    font-weight: 600;
  }

  .cd-turnout {
    margin-inline-start: auto;
    color: var(--muted);
  }

  .cd-columns {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 14px;
  }

  .cd-column {
    display: grid;
    gap: 3px;
    align-content: start;
  }

  .cd-label {
    color: var(--muted);
    font-size: 12px;
  }

  .cd-row {
    display: grid;
    grid-template-columns: 7.5em 1fr 3.6em 5.4em;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
  }

  .cd-party {
    overflow: hidden;
    font-weight: 500;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .cd-track {
    block-size: 6px;
    border-radius: 1px;
    background: color-mix(in oklab, var(--rule) 55%, transparent);
  }

  .cd-bar {
    display: block;
    block-size: 100%;
    border-radius: 1px;
    background: var(--party);
    transition: inline-size 240ms cubic-bezier(0.2, 0.8, 0.2, 1);
  }

  .cd-num {
    text-align: end;
    font-family: var(--font-num);
    font-variant-numeric: tabular-nums;
    font-size: 0.95em;
  }

  .cd-votes {
    color: var(--muted);
  }

  @media (prefers-reduced-motion: reduce) {
    .cd-bar {
      transition: none;
    }
  }
</style>
