<script lang="ts">
  import type { LiveRace } from "@ea/analytics/live";
  import { resolve } from "$app/paths";
  import ArrowDownIcon from "@lucide/svelte/icons/arrow-down";
  import ArrowUpIcon from "@lucide/svelte/icons/arrow-up";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import { isHover } from "#lib/explain.svelte.js";
  import { count, percent } from "#lib/format.js";
  import {
    compact,
    isDistricted,
    lead,
    placeName,
    placeSlug,
    tone,
  } from "#lib/live/format.js";
  import { standing } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { desk, race }: { desk: Desk; race: LiveRace } = $props();

  type Key = "name" | "margin" | "counted" | "votes";

  let key = $state<Key>("votes");
  let isAscending = $state(false);

  const detail = $derived(desk.feed.detail(race));
  const now = $derived(standing(detail.race, { at: null, isFinal: true }));
  const rows = $derived(
    [...now.units.values()].map((row) => {
      const expected = row.expected ?? row.baseline?.turnout ?? 0;
      return {
        row,
        counted:
          row.total === 0 || expected <= 0
            ? null
            : Math.min(1, row.total / expected),
        weight: row.total > 0 ? row.total : expected,
      };
    }),
  );
  const sorted = $derived(
    rows.toSorted((a, b) => {
      const order =
        key === "name"
          ? a.row.unit.name.localeCompare(b.row.unit.name)
          : key === "margin"
            ? (a.row.signed ?? -2) - (b.row.signed ?? -2)
            : key === "counted"
              ? (a.counted ?? -1) - (b.counted ?? -1)
              : a.weight - b.weight;
      return isAscending ? order : -order;
    }),
  );
  const base = $derived(desk.feed.statePath(race));

  const COLUMNS: Array<{ id: Key; label: () => string; isEnd: boolean }> = [
    {
      id: "name",
      label: () =>
        isDistricted(race.state)
          ? m.night_table_district()
          : m.board_counties_county(),
      isEnd: false,
    },
    { id: "margin", label: m.board_counties_margin, isEnd: true },
    { id: "counted", label: m.board_counties_in, isEnd: true },
    { id: "votes", label: m.board_counties_votes, isEnd: true },
  ];

  $effect(() => {
    desk.feed.want(race);
  });

  function sortBy(next: Key) {
    if (key === next) {
      isAscending = !isAscending;
      return;
    }
    key = next;
    isAscending = next === "name";
  }
</script>

{#if !desk.feed.hasDetail(race)}
  <p
    class="animate-pulse text-[0.75em] text-muted-foreground motion-reduce:animate-none"
  >
    {m.board_loading()}
  </p>
{:else}
  <table class="border-collapse text-[0.8125em] inline-full">
    <thead class="sticky inset-bs-0 z-10 bg-background">
      <tr class="border-be border-border">
        {#each COLUMNS as column (column.id)}
          <th
            class={cn("py-1 font-semibold text-muted-foreground", {
              "text-end": column.isEnd,
              "text-start": !column.isEnd,
            })}
            aria-sort={key === column.id
              ? isAscending
                ? "ascending"
                : "descending"
              : undefined}
          >
            <button
              type="button"
              class={cn(
                "inline-flex items-center gap-0.5 text-[0.85em] hover:text-foreground",
                { "flex-row-reverse": column.isEnd },
              )}
              onclick={() => sortBy(column.id)}
            >
              {column.label()}
              {#if key === column.id}
                {#if isAscending}
                  <ArrowUpIcon class="block-3 inline-3" aria-hidden="true" />
                {:else}
                  <ArrowDownIcon class="block-3 inline-3" aria-hidden="true" />
                {/if}
              {/if}
            </button>
          </th>
        {/each}
      </tr>
    </thead>
    <tbody>
      {#each sorted as entry (entry.row.unit.id)}
        {const row = $derived(entry.row)}
        {const option = $derived(
          row.leader == null ? undefined : detail.race.options[row.leader],
        )}
        <tr
          class={cn("border-be border-border/60 transition-colors", {
            "bg-accent": desk.unit === row.unit.id,
            "hover:bg-muted": desk.unit !== row.unit.id,
          })}
          onpointerenter={(event) => {
            if (isHover(event)) desk.unit = row.unit.id;
          }}
          onpointerleave={() => {
            if (desk.unit === row.unit.id) {
              desk.unit = null;
            }
          }}
        >
          <td class="truncate py-1 pe-2 inline-full max-inline-0">
            <a
              class="hover:underline"
              href={resolve("/live/[...path]", {
                path: `${base}/place/${placeSlug(row.unit.name)}`,
              })}>{placeName(row.unit.name)}</a
            >
          </td>
          <td class="py-1 ps-2 text-end whitespace-nowrap">
            {#if option == null}
              <span class="text-muted-foreground">–</span>
            {:else}
              <span class="inline-flex items-center gap-1">
                <span
                  class="rounded-full block-2 inline-2"
                  style:background-color={tone(option)}
                ></span>
                <span class="font-figures tabular-nums"
                  >+{lead(row.margin)}</span
                >
              </span>
            {/if}
          </td>
          <td
            class="py-1 ps-2 text-end font-figures text-muted-foreground tabular-nums"
          >
            {entry.counted == null ? "–" : percent(entry.counted, 0)}
          </td>
          <td
            class="py-1 ps-2 text-end font-figures tabular-nums"
            title={count(row.total)}
            >{row.total > 0 ? compact(row.total) : "–"}</td
          >
        </tr>
      {/each}
    </tbody>
  </table>
{/if}
