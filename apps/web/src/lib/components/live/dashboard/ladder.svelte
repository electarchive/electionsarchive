<script lang="ts">
  import type { ChamberView, Family, Seat } from "#lib/live/nation.js";
  import { lead } from "#lib/live/format.js";
  import { odds } from "#lib/live/nation.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    view,
    sides,
    forecasts,
    label,
    href,
    tint,
  }: {
    view: ChamberView;
    sides: [Family, Family];
    forecasts: boolean;
    label: (seat: Seat) => string;
    href: (seat: Seat) => string;
    tint: (id: string | null) => string;
  } = $props();

  const left = $derived(sides[0]);
  const right = $derived(sides[1]);
  const SCALE = 0.2;

  let isAll = $state(false);

  const pivot = $derived(
    view.tipping == null ? -1 : view.order.indexOf(view.tipping),
  );
  const shown = $derived.by(() => {
    const rows = view.order.map((seat, index) => ({ seat, index }));
    if (isAll || rows.length <= 17) {
      return rows;
    }
    const center = pivot < 0 ? Math.floor(rows.length / 2) : pivot;
    const start = Math.max(0, Math.min(rows.length - 17, center - 8));
    return rows.slice(start, start + 17);
  });

  function place(value: number) {
    return 50 - (Math.max(-SCALE, Math.min(SCALE, value)) / SCALE) * 50;
  }
</script>

<section class="grid content-start gap-2">
  <div class="grid gap-0.5">
    <h2 class="heading text-lg!">{m.night_ladder_title()}</h2>
    <p class="text-xs text-pretty text-muted-foreground">
      {view.needed <= 0
        ? m.night_ladder_secured({ party: left.name })
        : m.night_ladder_help({
            left: left.short,
            right: right.short,
            party: left.name,
            count: view.needed,
          })}
    </p>
  </div>
  <ol class="grid text-[0.8125rem]">
    {#each shown as row (row.seat.summary.race.id)}
      {const seat = $derived(row.seat)}
      {const isPivot = $derived(row.index === pivot)}
      {const chance = $derived(
        seat.left >= seat.right ? seat.left : seat.right,
      )}
      <li
        class={cn({
          "bg-[color-mix(in_oklab,var(--foreground)_6%,var(--background))]":
            isPivot,
        })}
      >
        {#if isPivot}
          <span
            class="flex items-center gap-2 border-bs-2 border-foreground px-1 pbs-0.5 text-[0.6875rem] font-semibold"
            >{m.night_ladder_tipping()}</span
          >
        {/if}
        <a
          href={href(seat)}
          class="grid grid-cols-[2.25rem_minmax(0,6.5rem)_minmax(0,1fr)_3.25rem] items-center gap-2 border-be border-border p-1 hover:highlighter"
        >
          <span
            class="text-end font-figures text-xs text-muted-foreground tabular-nums"
            title={m.night_ladder_rank({ party: left.short.slice(0, 1) })}
            >{view.tallies.left.holdover + row.index + 1}</span
          >
          <span class="flex items-center gap-1.5 min-inline-0">
            <span
              class="shrink-0 rounded-full block-1.5 inline-1.5"
              style:background-color={tint(seat.held)}
              title={m.night_ladder_held()}
            ></span>
            <span class="truncate font-semibold">{label(seat)}</span>
          </span>
          <span class="relative block-4" aria-hidden="true">
            <span class="absolute inset-y-0 inset-s-1/2 border-s border-border"
            ></span>
            {#if seat.signed == null}
              <span class="absolute inset-x-0 inset-y-1.5 rounded-full bg-muted"
              ></span>
            {:else}
              {#if seat.band != null}
                <span
                  class="absolute inset-y-1.75 rounded-full opacity-40"
                  style:inset-inline-start="{Math.min(
                    place(seat.band.low),
                    place(seat.band.high),
                  )}%"
                  style:inline-size="{Math.abs(
                    place(seat.band.high) - place(seat.band.low),
                  )}%"
                  style:background-color={seat.signed >= 0
                    ? left.color
                    : right.color}
                ></span>
              {/if}
              <span
                class="absolute inset-y-1"
                style:inset-inline-start="{Math.min(50, place(seat.signed))}%"
                style:inline-size="{Math.abs(place(seat.signed) - 50)}%"
                style:background-color={seat.signed >= 0
                  ? left.color
                  : right.color}
              ></span>
            {/if}
          </span>
          <span class="text-end font-figures text-xs tabular-nums">
            {#if forecasts && seat.rating !== "counting" && seat.rating !== "waiting" && seat.rating !== "won" && seat.rating !== "projected"}
              <span
                style:color={seat.left >= seat.right ? left.color : right.color}
                >{odds(chance)}</span
              >
            {:else if seat.signed != null}
              <span style:color={seat.signed >= 0 ? left.color : right.color}
                >{seat.signed >= 0
                  ? left.short.slice(0, 1)
                  : right.short.slice(0, 1)}+{lead(seat.signed)}</span
              >
            {:else}
              <span class="text-muted-foreground">–</span>
            {/if}
          </span>
        </a>
      </li>
    {/each}
  </ol>
  {#if view.order.length > 17}
    <button
      type="button"
      class="justify-self-start text-xs font-semibold underline decoration-current/40 underline-offset-[0.2em] hover:decoration-current"
      onclick={() => (isAll = !isAll)}
      >{isAll
        ? m.night_ladder_fewer()
        : m.night_ladder_all({ count: view.order.length })}</button
    >
  {/if}
</section>
