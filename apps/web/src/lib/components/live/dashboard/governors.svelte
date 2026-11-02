<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Point } from "#lib/live/hover.js";
  import type { ChamberView, Family, Seat } from "#lib/live/nation.js";
  import type { Summary } from "#lib/live/summary.js";
  import { pointAt } from "#lib/live/hover.js";
  import { isNarrowLayout } from "#lib/narrow-layout.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Card from "./card.svelte";
  import Sheet from "./sheet.svelte";

  const {
    view,
    sides,
    title,
    label,
    name,
    href,
    shade,
    detail,
    line,
  }: {
    view: ChamberView;
    sides: [Family, Family];
    title: string;
    label: (seat: Seat) => string;
    name: (seat: Seat) => string;
    href: (seat: Seat) => string;
    shade: (seat: Seat) => string;
    detail: Snippet<[Seat]>;
    line: Snippet<[Summary | undefined]>;
  } = $props();

  let point = $state<Point | null>(null);
  let isOpen = $state(false);
  const narrow = $derived(isNarrowLayout());
  const net = $derived(view.tallies.left.gains - view.tallies.left.losses);

  function signed(value: number) {
    return value > 0 ? `+${value}` : value < 0 ? `−${Math.abs(value)}` : "0";
  }
</script>

{#snippet tiles(isLinked: boolean)}
  <ol
    class="grid grid-cols-[repeat(auto-fill,minmax(2.5rem,1fr))] gap-1"
    aria-label={m.night_governors_label({
      left: sides[0].name,
      right: sides[1].name,
    })}
  >
    {#each view.order as seat, index (seat.summary.race.id)}
      {const isStrong = $derived(
        ["won", "solid", "likely"].includes(seat.rating) && seat.side != null,
      )}
      <li class="grid">
        <svelte:element
          this={isLinked ? "a" : "span"}
          href={isLinked ? href(seat) : undefined}
          data-cell={index}
          class={cn(
            "grid place-items-center rounded-xs font-figures text-[0.6875rem] font-semibold tracking-wide uppercase block-8",
            {
              "hover:outline-1 hover:outline-offset-1 hover:outline-foreground focus-visible:outline-2 focus-visible:outline-foreground":
                isLinked,
            },
            { "text-white": isStrong, "text-foreground": !isStrong },
            { "shadow-[inset_0_0_0_2px_var(--foreground)]": seat.isFlip },
          )}
          style:background-color={shade(seat)}
          aria-label={isLinked ? label(seat) : undefined}
          >{seat.summary.race.state}</svelte:element
        >
      </li>
    {/each}
  </ol>
{/snippet}

{#snippet legend()}
  <dl class="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-0.5 text-xs">
    {#each [{ family: sides[0], tally: view.tallies.left }, { family: sides[1], tally: view.tallies.right }] as row (row.family.id)}
      <dt class="flex items-center gap-1.5">
        <span
          class="rounded-full block-2 inline-2"
          style:background-color={row.family.color}
        ></span>{row.family.name}
      </dt>
      <dd class="font-figures text-muted-foreground tabular-nums">
        {m.night_chamber_breakdown({
          won: row.tally.won,
          ahead: row.tally.ahead,
          holdover: row.tally.holdover,
        })}
      </dd>
      <dd
        class={cn("text-end font-figures font-semibold tabular-nums", {
          "text-muted-foreground": row.tally.gains === row.tally.losses,
        })}
      >
        {signed(row.tally.gains - row.tally.losses)}
      </dd>
    {/each}
  </dl>
{/snippet}

<section class="grid content-start gap-3 bg-background px-4 py-5 sm:px-6">
  <header class="flex items-baseline justify-between gap-3">
    <h2 class="heading text-lg!">{title}</h2>
    <p class="text-xs text-muted-foreground">
      {m.night_governors_races({ count: view.order.length })}
    </p>
  </header>

  {#if narrow}
    <button
      type="button"
      class="grid gap-2 text-start"
      aria-label={m.night_chamber_open({ title })}
      onclick={() => {
        isOpen = true;
      }}
    >
      {@render tiles(false)}
      <span class="text-xs text-muted-foreground">{m.night_chamber_tap()}</span>
    </button>
  {:else}
    <div
      class="relative"
      role="presentation"
      onpointermove={(event) => {
        point = pointAt(event);
      }}
      onpointerleave={() => {
        point = null;
      }}
    >
      {@render tiles(true)}
      {#if point != null}
        {const seat = $derived(view.order[point.index])}
        {#if seat != null}
          <Card {point}>{@render detail(seat)}</Card>
        {/if}
      {/if}
    </div>
  {/if}

  {@render legend()}

  <p class="text-xs text-muted-foreground">
    {net === 0
      ? m.night_governors_net_none()
      : m.night_chamber_net({
          party: net > 0 ? sides[0].name : sides[1].name,
          count: Math.abs(net),
        })}
  </p>
</section>

{#if narrow}
  <Sheet
    bind:isOpen
    {title}
    note={m.night_governors_races({ count: view.order.length })}
    seats={view.order}
    {name}
    {href}
    {shade}
    {line}
    summary={legend}
  />
{/if}
