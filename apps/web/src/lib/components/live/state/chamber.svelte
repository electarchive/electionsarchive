<script lang="ts">
  import SearchIcon from "@lucide/svelte/icons/search";
  import type { Entry, Seating, Side } from "#lib/live/ballot.js";
  import type { Flip } from "#lib/live/flips.js";
  import type { Geography } from "#lib/live/standing.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { count } from "#lib/format.js";
  import { district } from "#lib/live/ballot.js";
  import { FLIP_ORDER } from "#lib/live/flips.js";
  import { fold, tokenize } from "#lib/live/local.js";
  import { m } from "#lib/paraglide/messages.js";
  import Bar from "./bar.svelte";
  import Districts from "./districts.svelte";
  import Rows from "./rows.svelte";

  const {
    name,
    entries,
    seats,
    href,
    label,
    geography,
    shade,
  }: {
    name: string;
    entries: Array<Entry>;
    seats: Seating;
    href: (entry: Entry) => string;
    label: (entry: Entry) => string;
    geography: Geography | null;
    shade: (entry: Entry) => string;
  } = $props();

  const PAGE = 12;

  let query = $state("");
  let filter = $state<"all" | Flip>("all");
  let order = $state<"district" | "flips" | "margin">("district");
  let isOpen = $state(false);

  const up = $derived.by(() => {
    let found = 0;
    for (const entry of entries) {
      found += entry.contest.seats;
    }
    return found;
  });
  const tokens = $derived(tokenize(query));
  const changing = $derived(
    entries.filter(
      (entry) => entry.shift != null && entry.shift.flip !== "held",
    ).length,
  );
  const filters = $derived([
    {
      value: "all" as const,
      label: m.night_local_all(),
      count: entries.length,
    },
    ...(
      [
        ["flipped", m.night_flip_status_flipped],
        ["leading", m.night_flip_status_leading],
        ["held", m.night_flip_status_held],
      ] as const
    )
      .map(([value, name]) => ({
        value,
        label: name(),
        count: entries.filter((entry) => entry.shift?.flip === value).length,
      }))
      .filter((option) => option.count > 0),
  ]);
  const active = $derived(
    filters.some((option) => option.value === filter) ? filter : "all",
  );
  const matched = $derived(
    entries.filter(
      (entry) =>
        (active === "all" || entry.shift?.flip === active) &&
        (tokens.length === 0 ||
          tokens.every((token) =>
            fold(
              [
                label(entry),
                entry.contest.seat ?? "",
                ...entry.contest.options.map((option) => option.name),
              ].join(" "),
            ).includes(token),
          )),
    ),
  );
  const sorted = $derived(
    order === "district"
      ? matched
      : matched.toSorted((a, b) =>
          order === "flips"
            ? flipRank(b) - flipRank(a) ||
              a.summary.margin - b.summary.margin ||
              district(a) - district(b)
            : Number(a.summary.total === 0) - Number(b.summary.total === 0) ||
              a.summary.margin - b.summary.margin,
        ),
  );

  function flipRank(entry: Entry) {
    return entry.shift == null ? -1 : FLIP_ORDER[entry.shift.flip];
  }
  const shown = $derived(
    isOpen || tokens.length > 0 ? sorted : sorted.slice(0, PAGE),
  );
  const orders = $derived([
    { value: "district" as const, label: m.night_state_sort_district() },
    { value: "flips" as const, label: m.night_state_sort_flips() },
    { value: "margin" as const, label: m.night_state_sort_margin() },
  ]);
  const ordered = $derived(
    [seats.left, ...seats.rest, seats.right].filter(
      (side): side is Side => side != null,
    ),
  );
</script>

<section class="grid content-start gap-4 bg-background px-4 py-5 sm:px-8">
  <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
    <h3 class="heading text-lg! text-pretty">{name}</h3>
    <p class="font-figures text-xs text-muted-foreground tabular-nums">
      {m.night_state_seats_up({ count: count(up) })}
      <span aria-hidden="true">/</span>
      {m.night_chamber_majority({ count: count(seats.majority) })}
    </p>
  </div>

  <Bar {seats} />

  <table class="border-collapse font-figures text-[0.8125rem] tabular-nums">
    <thead>
      <tr class="border-be border-foreground text-xs">
        <th scope="col" class="py-1 text-start font-semibold"></th>
        <th scope="col" class="py-1 text-end font-semibold"
          >{m.night_state_before()}</th
        >
        <th scope="col" class="py-1 text-end font-semibold"
          >{m.night_state_not_up()}</th
        >
        <th scope="col" class="py-1 text-end font-semibold"
          >{m.night_state_now()}</th
        >
      </tr>
    </thead>
    <tbody>
      {#each ordered as side (side.id)}
        {const now = $derived(side.won + side.ahead)}
        {const change = $derived(now + side.holdover - side.before)}
        <tr class="border-be border-border">
          <th scope="row" class="py-1 text-start font-sans font-semibold">
            <span class="flex items-center gap-1.5">
              <span
                class="shrink-0 rounded-[1px] block-2.5 inline-2.5"
                style:background-color={side.color}
              ></span>
              <span class="truncate">{side.name || side.short}</span>
            </span>
            {#if side.makeup != null}
              <span class="block ps-4 text-xs font-normal text-muted-foreground"
                >{side.makeup}</span
              >
            {/if}
          </th>
          <td class="py-1 text-end">{count(side.before)}</td>
          <td class="py-1 text-end text-muted-foreground"
            >{count(side.holdover)}</td
          >
          <td class="py-1 text-end font-semibold"
            >{seats.isCounting ? count(now) : "–"}
            {#if seats.waiting === 0 && seats.isCounting && change !== 0}
              <span
                class="ms-1 text-xs"
                style:color="color-mix(in oklab, {side.color} 75%, var(--foreground))"
                >{change > 0 ? "+" : "−"}{Math.abs(change)}</span
              >
            {/if}</td
          >
        </tr>
      {/each}
    </tbody>
  </table>
  {#if geography != null}
    <Districts {geography} {entries} {name} {label} {href} {shade} />
  {/if}

  <div class="grid gap-2">
    <div class="flex flex-wrap items-center gap-2">
      <label
        class="flex grow basis-56 items-center gap-2 rounded-sm border border-input bg-card px-2.5 block-8 focus-within:border-foreground focus-within:ring-2 focus-within:ring-ring/40"
      >
        <SearchIcon
          class="shrink-0 text-muted-foreground block-4 inline-4"
          aria-hidden="true"
        />
        <input
          bind:value={query}
          type="search"
          enterkeyhint="search"
          autocomplete="off"
          spellcheck="false"
          class="flex-1 bg-transparent text-sm outline-none min-inline-0"
          placeholder={m.night_state_search()}
          aria-label={m.night_state_search()}
        />
      </label>
      {#if changing > 0}
        <Segmented
          options={filters}
          bind:value={() => active, (value: "all" | Flip) => (filter = value)}
          label={m.night_state_filter()}
          size="sm"
        />
      {/if}
      <Segmented
        options={orders}
        bind:value={order}
        label={m.night_state_sort()}
        size="sm"
      />
    </div>
    {#if matched.length === 0}
      <p class="py-3 text-sm text-muted-foreground">
        {m.night_state_none({ query: query.trim() })}
      </p>
    {:else}
      <Rows
        entries={shown}
        heading={m.night_state_district()}
        {label}
        {href}
        {tokens}
      />
      {#if tokens.length === 0 && matched.length > PAGE}
        <Button
          variant="outline"
          size="sm"
          class="justify-self-start"
          onclick={() => (isOpen = !isOpen)}
          >{isOpen
            ? m.night_state_less()
            : m.night_state_more({ count: count(matched.length) })}</Button
        >
      {/if}
    {/if}
  </div>
</section>
