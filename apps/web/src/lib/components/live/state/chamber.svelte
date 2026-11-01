<script lang="ts">
  import SearchIcon from "@lucide/svelte/icons/search";
  import type { Entry, Seating, Side } from "#lib/live/ballot.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { count } from "#lib/format.js";
  import { fold, tokenize } from "#lib/live/local.js";
  import { m } from "#lib/paraglide/messages.js";
  import Bar from "./bar.svelte";
  import Rows from "./rows.svelte";

  const {
    name,
    entries,
    seats,
    href,
    label,
  }: {
    name: string;
    entries: Array<Entry>;
    seats: Seating;
    href: (entry: Entry) => string;
    label: (entry: Entry) => string;
  } = $props();

  const PAGE = 12;

  let query = $state("");
  let filter = $state<"all" | "flips">("all");
  let isOpen = $state(false);

  const up = $derived.by(() => {
    let found = 0;
    for (const entry of entries) {
      found += entry.contest.seats;
    }
    return found;
  });
  const flips = $derived(entries.filter((entry) => entry.isFlip));
  const tokens = $derived(tokenize(query));
  const matched = $derived(
    (filter === "flips" ? flips : entries).filter(
      (entry) =>
        tokens.length === 0 ||
        tokens.every((token) =>
          fold(
            [
              label(entry),
              entry.contest.seat ?? "",
              ...entry.contest.options.map((option) => option.name),
            ].join(" "),
          ).includes(token),
        ),
    ),
  );
  const shown = $derived(
    isOpen || tokens.length > 0 ? matched : matched.slice(0, PAGE),
  );
  const filters = $derived([
    {
      value: "all" as const,
      label: m.night_local_all(),
      count: entries.length,
    },
    { value: "flips" as const, label: m.night_flip(), count: flips.length },
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
      {#if flips.length > 0}
        <Segmented
          options={filters}
          bind:value={filter}
          label={m.night_state_filter()}
          size="sm"
        />
      {/if}
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
