<script lang="ts">
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import type { LabView } from "./view.svelte.js";
  import { copy } from "./copy.js";
  import {
    constituencies,
    constituencyTurnout,
    laender,
    lead,
    map,
    partyColor,
    turnoutRange,
  } from "./election.js";

  const { view }: { view: LabView } = $props();

  const pickerItems = constituencies.map((entry) => ({
    value: entry.code,
    label: entry.name,
    detail: entry.code,
    group: laender[entry.land],
  }));

  const modes = [
    ["first", copy.mode_first],
    ["second", copy.mode_second],
    ["turnout", copy.mode_turnout],
  ] as const;

  const shapes = $derived(
    constituencies.map((entry) => {
      if (view.mode === "turnout") {
        const share =
          (constituencyTurnout(entry) - turnoutRange.low) /
          (turnoutRange.high - turnoutRange.low);
        return {
          entry,
          party: null,
          fill: `color-mix(in oklab, var(--map-ramp) ${Math.round(6 + share * 94)}%, var(--map-base))`,
        };
      }
      const leader = lead(entry, view.mode);
      return {
        entry,
        party: leader.party,
        fill: `color-mix(in oklab, ${partyColor(leader.party)} ${Math.round(30 + Math.min(leader.margin / 0.3, 1) * 70)}%, var(--map-base))`,
      };
    }),
  );
</script>

<div class="cmap">
  <div class="cmap-bar">
    <div class="cmap-modes" role="group" aria-label={copy.map_title}>
      {#each modes as [mode, label] (mode)}
        <button
          type="button"
          class={{ "cmap-mode": true, active: view.mode === mode }}
          aria-pressed={view.mode === mode}
          onclick={() => view.setMode(mode)}
        >
          {label}
        </button>
      {/each}
    </div>
    <Combobox
      class="cmap-picker"
      items={pickerItems}
      bind:value={view.selected}
      clearable
      label={copy.constituency_picker_label}
      placeholder={copy.constituency_picker_placeholder}
      search={copy.constituency_picker_search}
      empty={copy.constituency_picker_empty}
    />
  </div>
  <svg
    class="cmap-svg"
    viewBox="-4 -4 {map.width + 8} {map.height + 8}"
    role="img"
    aria-label={copy.map_title}
    onpointerleave={() => (view.hovered = null)}
  >
    <g class="cmap-units">
      {#each shapes as shape (shape.entry.code)}
        <path
          d={shape.entry.path}
          fill={shape.fill}
          class={{
            "cmap-unit": true,
            dim:
              view.party != null &&
              shape.party != null &&
              shape.party !== view.party,
          }}
          role="presentation"
          onpointerenter={() => (view.hovered = shape.entry.code)}
          onclick={() => view.toggleSelected(shape.entry.code)}
        />
      {/each}
    </g>
    <g class="cmap-laender">
      {#each Object.entries(map.laender) as [code, path] (code)}
        <path d={path} />
      {/each}
    </g>
    {#if view.focused != null}
      <path class="cmap-halo" d={view.focused.path} />
      <path class="cmap-focus" d={view.focused.path} />
    {/if}
  </svg>
  <div class="cmap-legend">
    <span class="cmap-ramp" class:turnout={view.mode === "turnout"}></span>
    <span
      >{view.mode === "turnout"
        ? copy.legend_turnout
        : copy.legend_margin}</span
    >
  </div>
</div>

<style>
  .cmap {
    display: grid;
    gap: 10px;
    min-inline-size: 0;
  }

  .cmap-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .cmap-modes {
    display: inline-flex;
    gap: 2px;
    padding: 2px;
    border: 1px solid var(--rule);
    border-radius: var(--radius-sm);
    background: var(--surface-2);
  }

  .cmap-mode {
    padding: 3px 9px;
    border-radius: calc(var(--radius-sm) - 2px);
    color: var(--muted);
    font: 500 12.5px/1.3 var(--font-ui);
    cursor: pointer;
  }

  .cmap-mode.active {
    background: var(--surface);
    color: var(--ink);
    box-shadow: 0 0 0 1px var(--rule);
  }

  .cmap-mode:focus-visible,
  .cmap-bar :global(.cmap-picker) {
    inline-size: 15rem;
    max-inline-size: 100%;
    font-family: var(--font-ui);
  }

  .cmap-svg {
    display: block;
    inline-size: 100%;
    block-size: auto;
    max-block-size: var(--map-max, 640px);
  }

  .cmap-unit {
    stroke: var(--map-stroke);
    stroke-width: 0.6px;
    vector-effect: non-scaling-stroke;
    transition: opacity 160ms ease;
    cursor: pointer;
  }

  .cmap-unit.dim {
    opacity: 0.2;
  }

  .cmap-laender path {
    fill: none;
    stroke: var(--map-land);
    stroke-width: 1.2px;
    vector-effect: non-scaling-stroke;
    pointer-events: none;
  }

  .cmap-halo,
  .cmap-focus {
    fill: none;
    vector-effect: non-scaling-stroke;
    pointer-events: none;
  }

  .cmap-halo {
    stroke: var(--halo);
    stroke-width: 5px;
  }

  .cmap-focus {
    stroke: var(--accent);
    stroke-width: 2px;
  }

  .cmap-legend {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font: 12px/1.3 var(--font-ui);
  }

  .cmap-ramp {
    inline-size: 72px;
    block-size: 8px;
    border-radius: 2px;
    background: linear-gradient(
      to right,
      color-mix(in oklab, var(--ink) 30%, var(--map-base)),
      var(--ink)
    );
  }

  .cmap-ramp.turnout {
    background: linear-gradient(
      to right,
      color-mix(in oklab, var(--map-ramp) 6%, var(--map-base)),
      var(--map-ramp)
    );
  }
</style>
