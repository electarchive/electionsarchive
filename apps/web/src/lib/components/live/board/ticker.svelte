<script lang="ts">
  import CircleDashedCheckIcon from "@lucide/svelte/icons/circle-dashed-check";
  import { prefersReducedMotion } from "svelte/motion";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import { short } from "#lib/live/board/labels.js";
  import { clock, compact, lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { desk }: { desk: Desk } = $props();

  const entries = $derived(
    desk.feed.night.updates.flatMap((update) => {
      const race = desk.feed.bySlug.get(update.race);
      return race == null
        ? []
        : [
            {
              ...update,
              race,
              leader: race.options.find(({ id }) => id === update.leader),
            },
          ];
    }),
  );

  function flash(_node: Element) {
    return {
      duration: prefersReducedMotion.current ? 0 : 1200,
      css: (t: number) =>
        `background-color: color-mix(in oklab, var(--accent) ${Math.round((1 - t) * 70)}%, transparent)`,
    };
  }

  const whenWidth = $derived(
    `${Math.max(0, ...entries.map((entry) => clock(entry.at).length))}ch`,
  );
</script>

{#if entries.length === 0}
  <p class="text-[0.75em] text-muted-foreground">{m.board_ticker_empty()}</p>
{:else}
  <ol class="grid text-[0.8125em]" style:--when={whenWidth}>
    {#each entries as entry (`${entry.race.id}:${entry.at}:${entry.isCall}`)}
      {const isPicked = $derived(desk.selected?.id === entry.race.id)}
      <li in:flash>
        <button
          type="button"
          class={cn(
            "grid grid-cols-[var(--when)_minmax(0,1fr)_auto] items-center gap-x-2 border-be border-border/60 p-1 text-start inline-full",
            { "bg-accent": isPicked, "hover:bg-muted": !isPicked },
          )}
          onclick={() => desk.select(entry.race.slug)}
        >
          <span
            class="font-figures whitespace-nowrap text-muted-foreground tabular-nums"
            >{clock(entry.at)}</span
          >
          <span class="flex items-center gap-1.5 min-inline-0">
            <span class="truncate font-semibold">{short(entry.race)}</span>
            <span
              class="shrink-0 rounded-full block-2 inline-2"
              style:background-color={tone(entry.leader)}
            ></span>
            <span class="truncate">{surname(entry.leader)}</span>
            {#if !entry.isCall && entry.margin != null}
              <span class="font-figures text-muted-foreground tabular-nums"
                >+{lead(entry.margin)}</span
              >
            {/if}
          </span>
          {#if entry.isCall}
            <span class="flex items-center gap-1 font-semibold">
              <CircleDashedCheckIcon
                class="shrink-0 block-[1.1em] inline-[1.1em]"
                aria-hidden="true"
              />
              {m.night_rating_projected()}
            </span>
          {:else}
            <span class="font-figures font-semibold tabular-nums"
              >+{compact(entry.added)}</span
            >
          {/if}
        </button>
      </li>
    {/each}
  </ol>
{/if}
