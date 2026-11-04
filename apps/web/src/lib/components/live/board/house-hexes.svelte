<script lang="ts">
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import type { Summary } from "#lib/live/summary.js";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import Hexes from "#lib/components/live/hexes.svelte";
  import { percent } from "#lib/format.js";
  import {
    ahead,
    districtKey,
    short,
    strength,
  } from "#lib/live/board/labels.js";
  import { hatch } from "#lib/live/flips.js";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import Mark from "./mark.svelte";

  const { desk }: { desk: Desk } = $props();

  let hovered = $state<string | null>(null);

  const districts = $derived(
    new Map<string, Summary>(
      [...desk.feed.summaries.values()].flatMap((summary) =>
        summary.race.office === "house"
          ? [[districtKey(summary.race), summary] as const]
          : [],
      ),
    ),
  );
  const turns = $derived(
    new Map(
      [...districts].flatMap(([key, summary]) => {
        const found = desk.shifts.get(summary.race.id);
        return found == null || found.flip === "held"
          ? []
          : [[key, { flip: found.flip, to: found.to }] as const];
      }),
    ),
  );
  const colors = $derived(
    Object.fromEntries(
      [...districts].map(([key, summary]) => {
        const turn = turns.get(key);
        return [
          key,
          turn == null
            ? strength(summary)
            : hatch(desk.party(turn.to).color, turn.flip).fill,
        ];
      }),
    ),
  );
  const flips = $derived(
    Object.fromEntries([...turns].map(([key, turn]) => [key, turn.flip])),
  );
</script>

{#snippet tip(key: string)}
  {const summary = $derived(districts.get(key))}
  {#if summary == null}
    <span class="font-semibold">{key.toUpperCase()}</span>
  {:else}
    {const option = $derived(ahead(summary))}
    <span class="font-semibold">{short(summary.race)}</span>
    {#if option == null}
      <span class="text-muted-foreground">{m.night_nation_waiting()}</span>
    {:else}
      <span class="flex items-center gap-1.5">
        <span
          class="rounded-full block-2 inline-2"
          style:background-color={tone(option)}
        ></span>
        {surname(option)}
        <span class="font-figures text-muted-foreground tabular-nums"
          >+{lead(summary.margin)}</span
        >
        <Mark {summary} />
      </span>
      {const found = $derived(desk.shifts.get(summary.race.id))}
      {#if found != null && found.flip !== "held"}
        <span
          ><Flip
            flip={found.flip}
            from={desk.party(found.from)}
            to={desk.party(found.to)}
            isLabeled={true}
          /></span
        >
      {/if}
    {/if}
    {#if summary.counted != null}
      <span class="text-muted-foreground"
        >{m.board_in({ share: percent(summary.counted, 0) })}</span
      >
    {/if}
  {/if}
{/snippet}

{#if desk.cartogram == null}
  <p class="text-[0.75em] text-muted-foreground">{m.night_nation_none()}</p>
{:else}
  <div class="block-full [&_svg]:block-full [&>div]:block-full">
    <Hexes
      cartogram={desk.cartogram}
      {colors}
      {flips}
      label={m.night_house_hexes_label()}
      bind:hovered
      href={() => null}
      onPick={(key: string) => {
        const summary = districts.get(key);
        if (summary != null) {
          desk.select(summary.race.slug);
        }
      }}
      {tip}
    />
  </div>
{/if}
