<script lang="ts">
  import type { LiveOption, LiveRace } from "@ea/analytics/live";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import MethodLink from "#lib/components/live/method-link.svelte";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { odds } from "#lib/live/nation.js";
  import { m } from "#lib/paraglide/messages.js";

  const { desk, race }: { desk: Desk; race: LiveRace } = $props();

  const isOff = $derived(!desk.feed.shown.forecasts);
  const estimate = $derived(desk.feed.summaries.get(race.id)?.estimate ?? null);
  const isReady = $derived(estimate?.status === "estimating");
  const first = $derived(
    race.options.find((option) => option.id === estimate?.leader),
  );
  const second = $derived(
    race.options.find((option) => option.id === estimate?.runnerUp),
  );
  const chance = $derived(
    first == null ? 0 : (estimate?.winProbability[first.id] ?? 0),
  );
  const favorite = $derived(chance >= 0.5 ? first : second);
  const favored = $derived(Math.max(chance, 1 - chance));
  const band = $derived(estimate?.margin ?? { low: 0, mid: 0, high: 0 });
  const reach = $derived(
    Math.max(0.05, Math.abs(band.low), Math.abs(band.high)) * 1.15,
  );

  function where(value: number) {
    return `${50 + (value / reach) * 50}%`;
  }

  function signed(
    value: number,
    ahead: LiveOption | undefined,
    behind: LiveOption | undefined,
  ) {
    const option = value >= 0 ? ahead : behind;
    return `${option?.party?.short ?? surname(option)}+${lead(value)}`;
  }
</script>

<div class="relative grid content-start gap-2 block-full">
  {#if isOff}
    <div
      class="pointer-events-none grid gap-2 opacity-35 select-none"
      aria-hidden="true"
    >
      <span class="block rounded-xs bg-muted block-7 inline-24"></span>
      <span class="block rounded-full bg-muted block-2.5"></span>
      <span class="block rounded-xs bg-muted block-4 inline-40"></span>
    </div>
    <p
      class="absolute inset-0 grid place-content-center px-3 text-center text-[0.75em] text-balance text-muted-foreground"
    >
      {m.board_forecast_off()}
    </p>
  {:else if estimate?.status === "ranked"}
    <p class="text-[0.75em] text-pretty text-muted-foreground">
      {m.board_chance_ranked()}
    </p>
  {:else if !isReady || first == null || second == null}
    <p class="text-[0.75em] text-muted-foreground">
      {m.board_chance_waiting()}
    </p>
  {:else}
    <p class="flex items-baseline gap-2">
      <span
        class="font-figures text-[1.75em] leading-none font-[760] tabular-nums"
        style:color={tone(favorite)}>{odds(favored)}</span
      >
      <span class="truncate font-semibold">{favorite?.label}</span>
      <span class="ms-auto self-center"><MethodLink section="ranges" /></span>
    </p>
    <div class="flex overflow-hidden rounded-full block-2.5" aria-hidden="true">
      <span
        class="transition-[inline-size] duration-700 motion-reduce:transition-none"
        style:inline-size="{chance * 100}%"
        style:background-color={tone(first)}
      ></span>
      <span class="flex-1" style:background-color={tone(second)}></span>
    </div>
    <div class="grid gap-1">
      <p
        class="flex flex-wrap justify-between gap-x-2 text-[0.6875em] text-muted-foreground"
      >
        <span>{m.board_chance_margin()}</span>
        <span class="font-figures tabular-nums"
          >{m.board_chance_range({
            low: signed(band.low, first, second),
            high: signed(band.high, first, second),
          })}</span
        >
      </p>
      <div class="relative rounded-xs bg-muted block-4" aria-hidden="true">
        <span
          class="absolute inset-y-0 opacity-40"
          style:inset-inline-start={where(Math.min(band.low, band.high))}
          style:inset-inline-end="calc(100% - {where(
            Math.max(band.low, band.high),
          )})"
          style:background-color={tone(band.mid >= 0 ? first : second)}
        ></span>
        <span
          class="absolute inset-y-0 border-s-2"
          style:inset-inline-start={where(band.mid)}
          style:border-color={tone(band.mid >= 0 ? first : second)}
        ></span>
        <span
          class="absolute -inset-y-0.5 inset-s-1/2 border-s border-foreground/60"
        ></span>
      </div>
      <p
        class="flex justify-between text-[0.6875em] text-muted-foreground"
        aria-hidden="true"
      >
        <span>{surname(second)}</span><span>{surname(first)}</span>
      </p>
    </div>
  {/if}
</div>
