<script lang="ts">
  import type { LiveRace } from "@ea/analytics/live";
  import { Pending } from "svelte-islands";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import type { UnitStanding } from "#lib/live/standing.js";
  import { unitHatch } from "#lib/components/live/full/model.js";
  import { percent } from "#lib/format.js";
  import { MUTED } from "#lib/live/board/labels.js";
  import { lead, placeName, surname, tone } from "#lib/live/format.js";
  import { standing } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import {
    marginSafety,
    marginShade,
    safety,
    swingSafety,
  } from "#lib/shade.js";
  import Atlas from "./atlas.svelte";

  const {
    desk,
    race,
    mode = "lead",
  }: {
    desk: Desk;
    race: LiveRace;
    mode?: "lead" | "shift" | "counted";
  } = $props();

  let hovered = $state<string | null>(null);

  const path = $derived(desk.feed.statePath(race));
  const shapes = $derived(desk.feed.shapes(path));
  const detail = $derived(desk.feed.detail(race));
  const now = $derived(standing(detail.race, { at: null, isFinal: true }));
  const options = $derived(detail.race.options);
  const toneA = $derived(tone(options[now.pair[0]]));
  const toneB = $derived(tone(options[now.pair[1]]));
  const byArea = $derived(
    new Map(
      [...now.units.values()].flatMap((row) =>
        row.unit.areaId == null ? [] : [[row.unit.areaId, row] as const],
      ),
    ),
  );
  const isStatewide = $derived(race.office !== "house");
  const focus = $derived(isStatewide ? null : new Set(byArea.keys()));
  const marked = $derived(
    desk.unit == null ? null : (now.units.get(desk.unit)?.unit.areaId ?? null),
  );

  $effect(() => {
    desk.feed.want(race);
    desk.feed.wantShapes(path);
  });

  function expectedOf(row: UnitStanding) {
    return row.baseline?.turnout ?? row.total;
  }

  function fill(row: UnitStanding) {
    if (mode === "shift") {
      if (row.signed == null || row.baseSigned == null) {
        return MUTED;
      }
      const shift = row.signed - row.baseSigned;
      return marginShade(shift >= 0 ? toneA : toneB, swingSafety(shift));
    }
    if (mode === "counted") {
      const share = Math.min(1, row.total / Math.max(1, expectedOf(row)));
      return row.total === 0
        ? MUTED
        : marginShade("var(--map-ink)", safety(0.08 + 0.8 * share));
    }
    if (row.leader == null) {
      return "color-mix(in oklab, var(--muted-foreground) 16%, var(--background))";
    }
    return (
      unitHatch(row, options)?.fill ??
      marginShade(tone(options[row.leader]), marginSafety(row.margin))
    );
  }

  const fills = $derived(
    Object.fromEntries([...byArea].map(([areaId, row]) => [areaId, fill(row)])),
  );
  const flips = $derived(
    mode === "lead"
      ? Object.fromEntries(
          [...byArea].flatMap(([areaId, row]) => {
            const stripe = unitHatch(row, options)?.stripe;
            return stripe == null ? [] : [[areaId, stripe] as const];
          }),
        )
      : {},
  );
</script>

{#snippet tip(areaId: string)}
  {const row = $derived(byArea.get(areaId))}
  {#if row != null}
    <span class="font-semibold">{placeName(row.unit.name)}</span>
    {#if row.leader == null}
      <span class="text-muted-foreground">{m.night_nation_waiting()}</span>
    {:else}
      <span class="flex items-center gap-1.5">
        <span
          class="rounded-full block-2 inline-2"
          style:background-color={tone(options[row.leader])}
        ></span>
        {surname(options[row.leader])}
        {#if row.isContested}
          <span class="font-figures text-muted-foreground tabular-nums"
            >+{lead(row.margin)}</span
          >
        {/if}
      </span>
      {#if row.signed != null && row.baseSigned != null}
        {const shift = $derived(row.signed - row.baseSigned)}
        <span class="text-muted-foreground"
          >{m.board_shift_toward({
            name: surname(options[shift >= 0 ? now.pair[0] : now.pair[1]]),
          })}
          <span class="font-figures tabular-nums">{lead(shift)}</span></span
        >
      {/if}
      {#if row.total > 0 && expectedOf(row) > 0}
        <span class="text-muted-foreground"
          >{m.board_in({
            share: percent(Math.min(1, row.total / expectedOf(row)), 0),
          })}</span
        >
      {/if}
    {/if}
  {/if}
{/snippet}

{#if shapes == null || !desk.feed.hasDetail(race)}
  <Pending
    name="live.board.county"
    isPending={true}
    reserve="100%"
    class="[--island-inset:0]"
  />
{:else}
  <Atlas
    {shapes}
    {fills}
    {flips}
    {focus}
    {marked}
    label={race.title}
    bind:hovered
    onPick={(areaId: string) => {
      const unitId = byArea.get(areaId)?.unit.id ?? null;
      desk.unit = desk.unit === unitId ? null : unitId;
    }}
    {tip}
  />
{/if}
