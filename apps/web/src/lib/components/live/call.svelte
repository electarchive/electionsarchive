<script lang="ts">
  import type { LiveOption } from "@ea/analytics/live";
  import type { Band } from "#lib/live/format.js";
  import type { Standing } from "#lib/live/standing.js";
  import Name from "#lib/components/name.svelte";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import BandLabel from "./band-label.svelte";
  import Face from "./face.svelte";

  const {
    band,
    options,
    now,
    isMeasure,
    at,
  }: {
    band: Band;
    options: Array<LiveOption>;
    now: Standing;
    isMeasure: boolean;
    at: string | null;
  } = $props();

  const called = $derived(
    options.flatMap((option, index) =>
      band.ids.includes(option.id)
        ? [
            {
              option,
              votes: now.totals[index] ?? 0,
              share: now.total > 0 ? (now.totals[index] ?? 0) / now.total : 0,
            },
          ]
        : [],
    ),
  );
  const isSingle = $derived(called.length === 1);
</script>

<div
  class="grid gap-3 band p-4 transition-colors duration-700 motion-reduce:transition-none sm:px-8"
  style:--party={band.fill}
>
  <BandLabel {band} {isMeasure} {at} class="text-[0.8125rem]" />
  <ul
    class={cn("flex flex-wrap gap-x-8 gap-y-3 max-inline-4xl", {
      "items-center justify-between": isSingle,
    })}
  >
    {#each called as entry (entry.option.id)}
      <li class="flex items-center gap-3 min-inline-0">
        <Face
          option={entry.option}
          color="var(--foreground)"
          class={cn({
            "block-12 inline-12 sm:block-14 sm:inline-14": isSingle,
            "block-9 inline-9": !isSingle,
          })}
        />
        <span class="grid gap-0.5 min-inline-0">
          <Name
            label={entry.option.label}
            person={entry.option.person}
            hasSwatch={false}
            class={cn("tracking-tight text-balance", {
              "text-[clamp(1.375rem,1.1rem+1vw,1.875rem)]/tight font-[780]":
                isSingle,
              "text-lg/tight font-[740]": !isSingle,
            })}
          />
          {#if entry.option.party != null}
            <span class="truncate text-[0.8125rem] text-muted-foreground">
              <Name
                label={entry.option.party.name}
                party={entry.option.party.id}
                color={entry.option.party.color}
                hasSwatch={false}
              />
            </span>
          {/if}
        </span>
      </li>
      {#if isSingle && now.total > 0}
        <li
          class="flex items-baseline gap-2.5 font-figures tabular-nums"
          aria-hidden="true"
        >
          <span
            class="text-[clamp(1.5rem,1.2rem+1vw,2rem)] leading-none font-[780] tracking-tight"
            >{percent(entry.share)}</span
          >
          <span class="text-[0.8125rem] text-muted-foreground"
            >{m.night_votes({ count: count(entry.votes) })}</span
          >
        </li>
      {/if}
    {/each}
  </ul>
</div>
