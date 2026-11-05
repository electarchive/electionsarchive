<script lang="ts">
  import type { LiveOffice } from "@ea/analytics/live";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import { surname, tone } from "#lib/live/format.js";
  import { party, tally } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";

  const { desk, office = "senate" }: { desk: Desk; office?: LiveOffice } =
    $props();

  const summaries = $derived([...desk.feed.summaries.values()]);
  const seats = $derived(
    summaries.filter((summary) => summary.race.office === office).length,
  );
  const balance = $derived(tally(summaries, office));
  const left = $derived(balance.groups[0]);
  const right = $derived(balance.groups[1]);
  const middle = $derived(balance.groups.slice(2));
  const ordered = $derived(left == null ? [] : [left, ...middle]);

  function name(group: (typeof balance.groups)[number]) {
    return group.option.party?.short ?? surname(group.option);
  }
</script>

{#if seats === 0}
  <p class="text-[0.75em] text-muted-foreground">{m.board_power_empty()}</p>
{:else}
  <div class="grid content-center gap-1.5 block-full">
    <div class="flex items-end justify-between gap-3">
      {#each [left, right] as group, index (index)}
        {#if group == null}
          <span></span>
        {:else}
          <span
            class="flex items-baseline gap-1.5"
            class:flex-row-reverse={index === 1}
          >
            <span
              class="font-figures text-[1.75em] leading-none font-[760] tabular-nums"
              style:color={tone(group.option)}>{group.seats}</span
            >
            <span
              class="grid text-[0.6875em] leading-tight"
              class:text-end={index === 1}
            >
              <span class="font-semibold">{name(group)}</span>
              <span class="font-figures text-muted-foreground tabular-nums"
                >{m.board_power_won()}
                {group.won}{#if group.projected > 0},
                  {m.night_rating_projected().toLocaleLowerCase()}
                  {group.projected}{/if}{#if group.unopposed > 0},
                  {m.night_state_unopposed().toLocaleLowerCase()}
                  {group.unopposed}{/if}, {m
                  .board_power_leading()
                  .toLocaleLowerCase()}
                {group.leading}</span
              >
            </span>
          </span>
        {/if}
      {/each}
    </div>
    <div
      class="relative flex overflow-hidden rounded-xs bg-muted block-3"
      aria-hidden="true"
    >
      {#each ordered as group (party(group.option))}
        <span
          class="transition-[inline-size] duration-700 motion-reduce:transition-none"
          style:inline-size="{((group.won + group.unopposed) / seats) * 100}%"
          style:background-color={tone(group.option)}
        ></span>
        <span
          class="transition-[inline-size] duration-700 motion-reduce:transition-none"
          style:inline-size="{(group.projected / seats) * 100}%"
          style:background-color="color-mix(in oklab, {tone(group.option)} 75%, var(--background))"
        ></span>
        <span
          class="transition-[inline-size] duration-700 motion-reduce:transition-none"
          style:inline-size="{(group.leading / seats) * 100}%"
          style:background-color="color-mix(in oklab, {tone(group.option)} 45%, var(--background))"
        ></span>
      {/each}
      <span class="flex-1"></span>
      {#if right != null}
        <span
          class="transition-[inline-size] duration-700 motion-reduce:transition-none"
          style:inline-size="{(right.leading / seats) * 100}%"
          style:background-color="color-mix(in oklab, {tone(right.option)} 45%, var(--background))"
        ></span>
        <span
          class="transition-[inline-size] duration-700 motion-reduce:transition-none"
          style:inline-size="{(right.projected / seats) * 100}%"
          style:background-color="color-mix(in oklab, {tone(right.option)} 75%, var(--background))"
        ></span>
        <span
          class="transition-[inline-size] duration-700 motion-reduce:transition-none"
          style:inline-size="{((right.won + right.unopposed) / seats) * 100}%"
          style:background-color={tone(right.option)}
        ></span>
      {/if}
      <span class="absolute -inset-y-0.5 inset-s-1/2 border-s border-foreground"
      ></span>
    </div>
    <p
      class="flex justify-between gap-2 font-figures text-[0.6875em] text-muted-foreground tabular-nums"
    >
      <span>{m.board_power_seats({ count: seats })}</span>
      {#if balance.waiting > 0}
        <span>{m.board_power_waiting({ count: balance.waiting })}</span>
      {/if}
    </p>
  </div>
{/if}
