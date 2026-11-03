<script lang="ts">
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    race,
    isCompact = false,
  }: { race: PageData["recent"][number]["lead"]; isCompact?: boolean } =
    $props();

  const isSeated = $derived(race != null && race.contested > 1);
</script>

<span class={cn("grid", { "gap-1.5": !isCompact, "gap-1": isCompact })}>
  {#if race != null}
    {#if race.question != null}
      <span
        class={cn("text-muted-foreground", {
          "line-clamp-2 text-[0.8125rem]": !isCompact,
          "line-clamp-1 text-xs": isCompact,
        })}>{race.question}</span
      >
    {/if}
    <span
      class={cn("gap-4", {
        "flex items-end justify-between": !isCompact,
        "grid grid-cols-2 items-baseline": isCompact,
      })}
    >
      {#each race.results.slice(0, 2) as row, at (row.key)}
        <span
          class={cn(
            "min-inline-0",
            { grid: !isCompact },
            {
              "flex items-baseline gap-2": isCompact,
              "flex-row-reverse": isCompact && at === 1,
            },
            { "justify-items-end text-end": at === 1 },
          )}
        >
          <span
            class={cn("font-figures leading-none", {
              "text-[1.75rem] font-[780] tracking-[-0.02em]": !isCompact,
              "shrink-0 text-base font-[760] tracking-[-0.01em] tabular-nums":
                isCompact,
            })}>{isSeated ? count(row.seats ?? 0) : percent(row.share)}</span
          >
          <span
            class={cn(
              "flex items-baseline gap-1.5 font-semibold max-inline-full min-inline-0",
              {
                "text-[0.875rem]": !isCompact,
                "text-[0.8125rem]": isCompact,
                "flex-row-reverse": isCompact && at === 1,
              },
            )}
          >
            <span
              class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
              style:background={swatch(row.color, row.key)}
            ></span>
            <span class="truncate"
              >{isSeated && row.person == null
                ? (row.short ?? row.label)
                : row.label}</span
            >
            {#if row.isLost === true}
              <span class="shrink-0 text-xs text-muted-foreground"
                >{race.quorum != null && row.share > 0.5
                  ? m.outcome_quorum()
                  : m.outcome_lost()}</span
              >
            {/if}
          </span>
        </span>
      {/each}
    </span>
    <span
      class={cn("relative flex overflow-hidden bg-muted", {
        "block-3": !isCompact,
        "block-1.5": isCompact,
      })}
    >
      {#each [race.results[0], ...race.results.slice(2), race.results[1]].filter((row) => row != null) as row (row.key)}
        <span
          style:inline-size="{(isSeated
            ? (row.seats ?? 0) / race.contested
            : row.share) * 100}%"
          style:background={swatch(row.color, row.key)}
        ></span>
      {/each}
      {#if isSeated}
        <span
          class="absolute -inset-y-px bg-foreground inline-0.5"
          style:inset-inline-start="{((Math.floor(race.contested / 2) + 1) /
            race.contested) *
            100}%"
        ></span>
      {/if}
    </span>
    {#if isSeated}
      <span class="sr-only"
        >{m.polity_seats({
          seats: count(race.results[0]?.seats ?? 0),
          size: count(race.contested),
        })}</span
      >
    {/if}
  {/if}
</span>
