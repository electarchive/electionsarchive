<script lang="ts">
  import type { Swing } from "#lib/live/flips.js";
  import { hatch, ink } from "#lib/live/flips.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    sides,
    changing,
    isProjected = false,
    notUp = 0,
  }: {
    sides: Array<{ id: string; short: string; color: string }>;
    changing: Swing;
    isProjected?: boolean;
    notUp?: number;
  } = $props();

  const SOLID = "oklch(0.42 0 0)";
  const NOT_UP =
    "color-mix(in oklab, var(--muted-foreground) 22%, var(--background))";
  const leading = $derived(hatch("var(--muted-foreground)", "leading"));
  const shown = $derived(
    sides.filter((side) =>
      changing.parties.some(
        (party) => party.id === side.id && party.flipped + party.leading > 0,
      ),
    ),
  );
</script>

{#snippet swatch(fill: string, stripes?: string)}
  <span
    class="shrink-0 rounded-[1px] block-3.5 inline-3.5"
    style:background-color={fill}
    style:background-image={stripes}
  ></span>
{/snippet}

{#if isProjected || notUp > 0 || shown.length > 0 || changing.leading > 0}
  <ul
    class="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground"
  >
    {#each shown as side (side.id)}
      {const flip = $derived(hatch(side.color, "flipped"))}
      <li class="flex items-center gap-1.5">
        <span class="font-semibold" style:color={side.color}>{side.short}</span>
        {@render swatch(side.color)}{m.night_rating_won()}
        {@render swatch(flip.fill, flip.stripes)}{m.night_flip_status_flipped()}
      </li>
    {/each}
    {#if changing.leading > 0}
      <li class="flex items-center gap-1.5">
        {@render swatch(
          leading.fill,
          leading.stripes,
        )}{m.night_chamber_leading_flip({ count: changing.leading })}
      </li>
    {/if}
    {#if isProjected}
      <li class="flex items-center gap-1.5">
        <span
          class="grid shrink-0 place-items-center rounded-[1px] block-3.5 inline-3.5"
          style:background-color={SOLID}
          ><span
            class="rounded-full block-1 inline-1"
            style:background-color={ink(SOLID)}
          ></span></span
        >{m.night_rating_projected()}
      </li>
    {/if}
    {#if notUp > 0}
      <li class="flex items-center gap-1.5">
        {@render swatch(NOT_UP)}{m.night_chamber_key_not_up()}
      </li>
    {/if}
  </ul>
{/if}
