<script lang="ts">
  import ArrowDownIcon from "@lucide/svelte/icons/arrow-down";
  import ArrowUpIcon from "@lucide/svelte/icons/arrow-up";
  import type { SegmentedOption } from "#lib/components/ui/segmented/index.js";
  import type { Family, Rating, Seat } from "#lib/live/nation.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { fits } from "#lib/fits.js";
  import { percent } from "#lib/format.js";
  import { clock, compact, lead, surname, tone } from "#lib/live/format.js";
  import { odds } from "#lib/live/nation.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import { Windowed } from "#lib/windowed.svelte.js";
  import Flip from "./flip.svelte";

  const {
    seats,
    sides,
    forecasts,
    label,
    href,
    tint,
    short,
    place,
  }: {
    seats: Array<Seat>;
    sides: [Family, Family];
    forecasts: boolean;
    label: (seat: Seat) => string;
    href: (seat: Seat) => string;
    tint: (id: string | null) => string;
    short: (id: string | null) => string;
    place: (code: string) => string;
  } = $props();

  type Office = "all" | "senate" | "governor" | "house";
  type Focus = "all" | "close" | "flips" | "upsets" | "left";
  type Key = "race" | "margin" | "counted" | "remaining" | "chance" | "at";

  const left = $derived(sides[0]);
  const right = $derived(sides[1]);
  const SCALE = 0.3;

  let office = $state<Office>("all");
  let focus = $state<Focus>("all");
  let query = $state("");
  let key = $state<Key>("margin");
  let isAscending = $state(true);

  const RATINGS: Record<Rating, () => string> = {
    won: m.night_rating_won,
    projected: m.night_rating_projected,
    solid: m.night_rating_solid,
    likely: m.night_rating_likely,
    lean: m.night_rating_lean,
    tossup: m.night_rating_tossup,
    counting: m.night_rating_counting,
    waiting: m.night_nation_waiting,
  };

  const TESTS: Record<Focus, (seat: Seat) => boolean> = {
    all: () => true,
    close: (seat) =>
      seat.summary.total > 0 &&
      seat.summary.margin < 0.02 &&
      seat.rating !== "won" &&
      seat.rating !== "projected",
    flips: (seat) => seat.isFlip,
    upsets: (seat) => seat.isUpset,
    left: (seat) => (seat.remaining ?? 0) > 0,
  };

  const byOffice = $derived(
    seats.filter(
      (seat) => office === "all" || seat.summary.race.office === office,
    ),
  );
  const words = $derived(
    query
      .trim()
      .toLowerCase()
      .split(/\s+/u)
      .filter((word) => word !== ""),
  );
  const matched = $derived(
    byOffice.filter((seat) => {
      if (!TESTS[focus](seat)) {
        return false;
      }
      if (words.length === 0) {
        return true;
      }
      const text = [
        label(seat),
        place(seat.summary.race.state),
        seat.summary.race.title,
        seat.holder,
        ...seat.summary.race.options.map((option) => option.label),
      ]
        .join(" ")
        .toLowerCase();
      return words.every((word) => text.includes(word));
    }),
  );

  function chance(seat: Seat) {
    return Math.max(seat.left, seat.right);
  }

  const VALUES: Record<Key, (seat: Seat) => number | string | null> = {
    race: (seat) => label(seat),
    margin: (seat) => (seat.summary.total === 0 ? null : seat.summary.margin),
    counted: (seat) => seat.summary.counted ?? null,
    remaining: (seat) => seat.remaining ?? null,
    chance: (seat) =>
      seat.rating === "counting" || seat.rating === "waiting"
        ? null
        : chance(seat),
    at: (seat) => seat.summary.at ?? null,
  };

  const sorted = $derived(
    matched.toSorted((a, b) => {
      const x = VALUES[key](a);
      const y = VALUES[key](b);
      if (x == null || y == null) {
        return (x == null ? 1 : 0) - (y == null ? 1 : 0);
      }
      const order =
        typeof x === "string" && typeof y === "string"
          ? x.localeCompare(y, undefined, { numeric: true })
          : Number(x) - Number(y);
      return isAscending ? order : -order;
    }),
  );
  const windowed = new Windowed(() => ({
    items: sorted,
    key: (seat) => seat.summary.race.id,
    size: 45,
  }));

  const OFFICES = $derived<Array<SegmentedOption<Office>>>([
    { value: "all", label: m.night_explorer_all(), count: seats.length },
    ...(["senate", "governor", "house"] as const).map((value) => ({
      value,
      label: {
        senate: m.night_nation_senate,
        governor: m.night_nation_governor,
        house: m.night_nation_house,
      }[value](),
      count: seats.filter((seat) => seat.summary.race.office === value).length,
    })),
  ]);
  const FOCUSES = $derived<Array<SegmentedOption<Focus>>>(
    (
      [
        ["all", m.night_explorer_any],
        ["close", m.night_explorer_close],
        ["flips", m.night_explorer_flips],
        ["upsets", m.night_explorer_upsets],
        ["left", m.night_explorer_left],
      ] as const
    ).map(([value, name]) => ({
      value,
      label: name(),
      count: byOffice.filter(TESTS[value]).length,
    })),
  );

  const COLUMNS = $derived<
    Array<{ id: Key | null; label: string; isEnd: boolean; width: number }>
  >([
    { id: "race", label: m.night_explorer_race(), isEnd: false, width: 12 },
    { id: null, label: m.night_explorer_held(), isEnd: false, width: 10 },
    { id: null, label: m.night_explorer_leader(), isEnd: false, width: 11 },
    { id: "margin", label: m.night_explorer_margin(), isEnd: false, width: 10 },
    {
      id: "counted",
      label: m.night_explorer_counted(),
      isEnd: false,
      width: 7.5,
    },
    {
      id: "remaining",
      label: m.night_explorer_remaining(),
      isEnd: true,
      width: 7,
    },
    ...(forecasts
      ? [
          {
            id: "chance" as const,
            label: m.night_explorer_chance(),
            isEnd: true,
            width: 9.5,
          },
        ]
      : []),
    { id: "at", label: m.night_explorer_updated(), isEnd: true, width: 6 },
  ]);
  const minimum = $derived.by(() => {
    let total = 0;
    for (const column of COLUMNS) total += column.width;
    return total;
  });

  function sortBy(next: Key) {
    if (key === next) {
      isAscending = !isAscending;
      return;
    }
    key = next;
    isAscending = next === "race" || next === "margin";
  }

  function place50(value: number) {
    return 50 - (Math.max(-SCALE, Math.min(SCALE, value)) / SCALE) * 50;
  }
</script>

<section
  class="grid grid-cols-[minmax(0,1fr)] gap-3 bg-background px-4 py-6 sm:px-8"
>
  <div class="flex flex-wrap items-end justify-between gap-3">
    <h2 class="heading text-lg!">{m.night_explorer_title()}</h2>
    <Input
      type="search"
      bind:value={query}
      placeholder={m.night_explorer_search()}
      aria-label={m.night_explorer_search()}
      class="text-[0.8125rem] block-8 inline-full sm:inline-72"
    />
  </div>
  <div class="flex flex-wrap items-center gap-2">
    <Segmented
      options={OFFICES}
      bind:value={office}
      label={m.night_explorer_office()}
      size="sm"
    />
    <Segmented
      options={FOCUSES}
      bind:value={focus}
      label={m.night_explorer_status()}
      size="sm"
    />
  </div>
  <div
    class="relative overflow-x-auto overflow-y-hidden overscroll-x-contain min-inline-0 not-data-fits:overflow-y-auto not-data-fits:max-block-svh data-fits:overflow-visible"
    {@attach fits}
  >
    <table
      class="table-fixed border-collapse text-[0.8125rem] inline-full"
      style:min-inline-size="{minimum}rem"
    >
      <colgroup>
        {#each COLUMNS as column (column.label)}
          <col style:inline-size="{column.width}rem" />
        {/each}
      </colgroup>
      <thead
        class="sticky inset-bs-0 z-2 bg-background [&_th]:shadow-[inset_0_-1px_0_var(--color-foreground)]"
      >
        <tr>
          {#each COLUMNS as column (column.label)}
            <th
              scope="col"
              class={cn("py-1.5 pe-3 font-semibold whitespace-nowrap", {
                "text-end": column.isEnd,
                "text-start": !column.isEnd,
              })}
              aria-sort={column.id != null && key === column.id
                ? isAscending
                  ? "ascending"
                  : "descending"
                : undefined}
            >
              {#if column.id == null}
                {column.label}
              {:else}
                {const id = $derived(column.id)}
                <button
                  type="button"
                  class={cn(
                    "inline-flex items-center gap-0.5 hover:underline",
                    { "flex-row-reverse": column.isEnd },
                  )}
                  onclick={() => sortBy(id)}
                >
                  {column.label}
                  {#if key === id}
                    {#if isAscending}
                      <ArrowUpIcon class="block-3 inline-3" />
                    {:else}
                      <ArrowDownIcon class="block-3 inline-3" />
                    {/if}
                  {/if}
                </button>
              {/if}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody {@attach windowed.anchor}>
        {#if windowed.before > 0}
          <tr aria-hidden="true">
            <td
              class="p-0"
              colspan={COLUMNS.length}
              style:block-size="{windowed.before}px"
            ></td>
          </tr>
        {/if}
        {#each windowed.entries as { value: seat, index, key: id } (id)}
          {const summary = $derived(seat.summary)}
          {const ahead = $derived(
            summary.winner ?? summary.projected ?? summary.leader,
          )}
          {const isLeft = $derived((seat.signed ?? 0) >= 0)}
          <tr
            class="border-be border-border hover:highlighter"
            {@attach windowed.measure(index)}
          >
            <th scope="row" class="py-1.5 pe-3 text-start font-normal">
              <a href={href(seat)} class="grid hover:underline">
                <span
                  class="flex items-center gap-1.5 font-semibold whitespace-nowrap"
                  >{label(seat)}
                  {#if seat.isFlip}
                    <Flip party={short(seat.side)} color={tint(seat.side)} />
                  {/if}
                </span>
                <span class="text-xs text-muted-foreground"
                  >{summary.race.office === "house"
                    ? place(summary.race.state)
                    : summary.race.office === "senate"
                      ? m.night_nation_senate()
                      : m.night_nation_governor()}</span
                >
              </a>
            </th>
            <td class="py-1.5 pe-3">
              <span class="grid">
                <span class="flex items-center gap-1.5">
                  <span
                    class="rounded-full block-2 inline-2"
                    style:background-color={tint(seat.held)}
                  ></span>{short(seat.held)}
                </span>
                <span
                  class="truncate text-xs text-muted-foreground max-inline-36"
                  >{seat.holder}</span
                >
              </span>
            </td>
            <td class="py-1.5 pe-3">
              {#if ahead == null}
                <span class="text-muted-foreground"
                  >{m.night_nation_waiting()}</span
                >
              {:else}
                <span class="grid">
                  <span class="flex items-center gap-1.5">
                    <span
                      class="rounded-full block-2 inline-2"
                      style:background-color={tone(ahead)}
                    ></span>
                    <span
                      class={cn({
                        "font-semibold":
                          summary.winner != null || summary.projected != null,
                      })}>{surname(ahead)}</span
                    >
                    {#if ahead.isIncumbent}
                      <span class="text-xs text-muted-foreground"
                        >{m.night_explorer_incumbent()}</span
                      >
                    {/if}
                  </span>
                  {#if summary.runnerUp != null}
                    <span class="text-xs text-muted-foreground"
                      >{m.night_explorer_over({
                        name: surname(summary.runnerUp),
                      })}</span
                    >
                  {/if}
                </span>
              {/if}
            </td>
            <td class="py-1.5 pe-3">
              <span class="flex items-center gap-2">
                <span
                  class="relative shrink-0 block-3 inline-24"
                  aria-hidden="true"
                >
                  <span
                    class="absolute inset-y-0 inset-s-1/2 border-s border-border"
                  ></span>
                  {#if seat.band != null}
                    <span
                      class="absolute inset-y-1.25 rounded-full opacity-35"
                      style:inset-inline-start="{Math.min(
                        place50(seat.band.low),
                        place50(seat.band.high),
                      )}%"
                      style:inline-size="{Math.abs(
                        place50(seat.band.high) - place50(seat.band.low),
                      )}%"
                      style:background-color={isLeft ? left.color : right.color}
                    ></span>
                  {/if}
                  {#if seat.signed != null}
                    <span
                      class="absolute inset-y-0.5"
                      style:inset-inline-start="{Math.min(
                        50,
                        place50(seat.signed),
                      )}%"
                      style:inline-size="{Math.abs(place50(seat.signed) - 50)}%"
                      style:background-color={isLeft ? left.color : right.color}
                    ></span>
                  {/if}
                </span>
                <span
                  class="font-figures tabular-nums"
                  style:color={summary.total === 0
                    ? undefined
                    : tone(ahead ?? undefined)}
                  >{summary.total === 0
                    ? "–"
                    : `+${lead(summary.margin)}`}</span
                >
              </span>
            </td>
            <td class="py-1.5 pe-3">
              <span class="flex items-center gap-2">
                <span
                  class="relative overflow-hidden rounded-xs bg-muted block-1.5 inline-12"
                  aria-hidden="true"
                >
                  <span
                    class="absolute inset-y-0 inset-s-0 bg-foreground"
                    style:inline-size="{(summary.counted ?? 0) * 100}%"
                  ></span>
                </span>
                <span class="font-figures text-muted-foreground tabular-nums"
                  >{summary.counted == null
                    ? "–"
                    : percent(summary.counted, 0)}</span
                >
              </span>
            </td>
            <td class="py-1.5 pe-3 text-end font-figures tabular-nums">
              {seat.remaining == null || seat.remaining < 1
                ? "–"
                : compact(seat.remaining)}
            </td>
            {#if forecasts}
              <td class="py-1.5 pe-3 text-end whitespace-nowrap">
                {#if seat.rating === "projected"}
                  <span
                    class="text-xs font-semibold"
                    style:color={seat.left >= seat.right
                      ? left.color
                      : right.color}>{RATINGS.projected()}</span
                  >
                {:else if seat.rating === "counting" || seat.rating === "waiting"}
                  <span class="text-muted-foreground"
                    >{RATINGS[seat.rating]()}</span
                  >
                {:else}
                  <span class="inline-flex items-baseline gap-1.5">
                    <span class="text-xs text-muted-foreground"
                      >{RATINGS[seat.rating]()}</span
                    >
                    <span
                      class="font-figures font-semibold tabular-nums"
                      style:color={seat.left >= seat.right
                        ? left.color
                        : right.color}>{odds(chance(seat))}</span
                    >
                  </span>
                {/if}
              </td>
            {/if}
            <td
              class="py-1.5 text-end font-figures text-muted-foreground tabular-nums"
              >{summary.at == null ? "–" : clock(summary.at)}</td
            >
          </tr>
        {:else}
          <tr>
            <td
              colspan={COLUMNS.length}
              class="py-8 text-center text-muted-foreground"
              >{m.night_explorer_empty()}</td
            >
          </tr>
        {/each}
        {#if windowed.after > 0}
          <tr aria-hidden="true">
            <td
              class="p-0"
              colspan={COLUMNS.length}
              style:block-size="{windowed.after}px"
            ></td>
          </tr>
        {/if}
      </tbody>
    </table>
  </div>
</section>
