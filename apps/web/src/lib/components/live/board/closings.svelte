<script lang="ts">
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import { clock, stamp, zoneName } from "#lib/live/format.js";
  import { ZONES } from "#lib/live/zones.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { desk }: { desk: Desk } = $props();

  const waves = $derived(
    [...Map.groupBy(desk.feed.election.pollClose, (entry) => entry.at)]
      .map(([at, entries]) => {
        const states = entries.map((entry) => entry.state).toSorted();
        return {
          at,
          time: Date.parse(at),
          states,
          local: [
            ...Map.groupBy(states, (code) =>
              stamp(at, ZONES[code] ?? "America/New_York"),
            ),
          ].map(([time, codes]) => ({ time, codes })),
        };
      })
      .toSorted((a, b) => a.time - b.time),
  );
  const next = $derived(waves.find((wave) => wave.time > desk.feed.clock));

  function left(time: number) {
    const seconds = Math.max(0, Math.round((time - desk.feed.clock) / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const rest = String(seconds % 60).padStart(2, "0");
    return hours > 0
      ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}`
      : `${minutes}:${rest}`;
  }

  function pick(code: string) {
    const races = desk.feed.election.races.filter(
      (race) => race.state === code,
    );
    const race =
      races.find((entry) => entry.office === "senate") ??
      races.find((entry) => entry.office === "governor") ??
      races[0];
    if (race != null) {
      desk.select(race.slug);
    }
  }
</script>

<div class="grid content-start gap-1.5 text-[0.8125em]">
  {#if waves[0] != null}
    <p class="text-[0.85em] text-muted-foreground">
      {m.night_closings_zone({ zone: zoneName(waves[0].at) })}
    </p>
  {/if}
  <ol class="grid gap-1">
    {#each waves as wave (wave.at)}
      {const isPast = $derived(wave.time <= desk.feed.clock)}
      {const isNext = $derived(next?.at === wave.at)}
      <li
        class={cn(
          "grid grid-cols-[4.5rem_minmax(0,1fr)] items-baseline gap-2 rounded-xs px-1 py-0.5",
          { "text-muted-foreground": isPast },
          { "bg-muted": isNext },
        )}
      >
        <span class="group relative grid justify-self-start">
          <button
            type="button"
            aria-describedby="board-closing-{wave.at}"
            class="cursor-help justify-self-start font-figures font-semibold tabular-nums underline decoration-current/45 decoration-dotted underline-offset-[0.2em]"
            >{clock(wave.at)}</button
          >
          <span class="font-figures text-[0.85em] tabular-nums">
            {#if isNext}
              {m.board_closings_in({ time: left(wave.time) })}
            {:else if isPast}
              {m.board_closings_closed()}
            {/if}
          </span>
          <span
            id="board-closing-{wave.at}"
            role="tooltip"
            class="pointer-events-none invisible absolute inset-s-0 inset-bs-full z-20 mbs-1 grid gap-1 rounded-sm bg-popover px-3 py-2 text-xs text-foreground opacity-0 shadow-menu ring-1 ring-border transition-opacity duration-150 min-inline-44 group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100 motion-reduce:transition-none"
          >
            <span class="font-semibold">{m.night_closings_local()}</span>
            {#each wave.local as entry (entry.time)}
              <span class="flex items-baseline justify-between gap-4">
                <span class="uppercase">{entry.codes.join(", ")}</span>
                <span
                  class="font-figures whitespace-nowrap text-muted-foreground tabular-nums"
                  >{entry.time}</span
                >
              </span>
            {/each}
          </span>
        </span>
        <span class="flex flex-wrap gap-0.5">
          {#each wave.states as code (code)}
            <button
              type="button"
              class="rounded-xs border border-border px-1 text-[0.85em] font-semibold uppercase hover:bg-background"
              onclick={() => pick(code)}>{code}</button
            >
          {/each}
        </span>
      </li>
    {/each}
  </ol>
</div>
