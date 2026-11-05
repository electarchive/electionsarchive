<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import ArrowDownIcon from "@lucide/svelte/icons/arrow-down";
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import ArrowRightIcon from "@lucide/svelte/icons/arrow-right";
  import ArrowUpIcon from "@lucide/svelte/icons/arrow-up";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import SearchIcon from "@lucide/svelte/icons/search";
  import type { Standing } from "#lib/live/standing.js";
  import { isHover } from "#lib/explain.svelte.js";
  import { count, percent } from "#lib/format.js";
  import { compact, lead, surname, tone } from "#lib/live/format.js";
  import { remainder } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  let {
    isDistrict = false,
    options,
    now,
    estimate,
    answers = null,
    href,
    selected = $bindable(null),
    hovered = $bindable(null),
  }: {
    isDistrict?: boolean;
    options: Array<LiveOption>;
    now: Standing;
    estimate: LiveEstimate | null;
    answers?: Array<number> | null;
    href?: (unitId: string) => string | null;
    selected?: string | null;
    hovered?: string | null;
  } = $props();

  type Key =
    | "name"
    | "margin"
    | "shares"
    | "base"
    | "shift"
    | "left"
    | "lean"
    | "counted"
    | "votes";

  const LIMIT = 12;

  let chosen = $state<Key | null>(null);
  let isAscending = $state(false);
  let query = $state("");
  let isAll = $state(false);

  const placeLabel = $derived(
    isDistrict ? m.night_table_district : m.night_table_county,
  );
  const searchLabel = $derived(
    isDistrict ? m.night_table_search_district() : m.night_table_search(),
  );
  const a = $derived(now.pair[0]);
  const b = $derived(now.pair[1]);
  const isPlural = $derived(answers != null);
  const isLive = $derived(estimate?.status === "estimating" && !now.isFinal);
  const key = $derived<Key>(chosen ?? (isLive ? "left" : "votes"));

  const rows = $derived(
    [...now.units.values()].map((row) => {
      const rest = remainder(row, estimate, options, now.pair);
      return {
        row,
        left: isLive ? rest.left : null,
        lean: isLive ? rest.lean : null,
        counted:
          rest.expected <= 0 ? null : Math.min(1, row.total / rest.expected),
        shift:
          row.signed == null || row.baseSigned == null
            ? null
            : row.signed - row.baseSigned,
      };
    }),
  );
  const value = (entry: (typeof rows)[number]): number | string => {
    switch (key) {
      case "name": {
        return entry.row.unit.name;
      }
      case "margin": {
        return isPlural
          ? entry.row.total > 0
            ? entry.row.margin
            : -Infinity
          : (entry.row.signed ?? -Infinity);
      }
      case "shares": {
        const first = answers?.[0] ?? 0;
        return entry.row.total > 0
          ? (entry.row.votes[first] ?? 0) / entry.row.total
          : -Infinity;
      }
      case "base": {
        return entry.row.baseSigned ?? -Infinity;
      }
      case "shift": {
        return entry.shift ?? -Infinity;
      }
      case "left": {
        return entry.left ?? -Infinity;
      }
      case "lean": {
        return entry.lean ?? -Infinity;
      }
      case "counted": {
        return entry.counted ?? -Infinity;
      }
      case "votes": {
        return entry.row.total + (entry.row.baseline?.turnout ?? 0) / 1e9;
      }
    }
  };
  const sorted = $derived.by(() => {
    const needle = query.trim().toLocaleLowerCase();
    const found =
      needle === ""
        ? rows
        : rows.filter((entry) =>
            entry.row.unit.name.toLocaleLowerCase().includes(needle),
          );
    const direction = isAscending ? 1 : -1;
    return found.toSorted((x, y) => {
      const left = value(x);
      const right = value(y);
      if (typeof left === "string" && typeof right === "string") {
        return left.localeCompare(right) * direction;
      }
      return (
        (Number(left) - Number(right)) * direction ||
        x.row.unit.name.localeCompare(y.row.unit.name)
      );
    });
  });
  const shown = $derived(
    isAll || query.trim() !== "" ? sorted : sorted.slice(0, LIMIT),
  );

  type Column = { id: Key; label: () => string; aria: () => string };
  const groups = $derived<
    Array<{ id: string; label: () => string; columns: Array<Column> }>
  >([
    {
      id: "counted",
      label: m.night_table_group_counted,
      columns: [
        ...(isPlural
          ? [
              {
                id: "margin" as const,
                label: m.night_table_leading,
                aria: m.night_table_leading,
              },
              {
                id: "shares" as const,
                label: m.night_table_shares,
                aria: () =>
                  m.night_table_shares_sort({
                    name: options[answers?.[0] ?? 0]?.label ?? "",
                  }),
              },
            ]
          : [
              {
                id: "margin" as const,
                label: m.night_table_margin,
                aria: m.night_table_margin,
              },
            ]),
        {
          id: "counted",
          label: m.night_table_counted,
          aria: m.night_table_counted,
        },
        { id: "votes", label: m.night_table_votes, aria: m.night_table_votes },
      ],
    },
    ...(isLive
      ? [
          {
            id: "left",
            label: m.night_table_group_left,
            columns: [
              {
                id: "left" as const,
                label: m.night_table_votes,
                aria: m.night_table_left,
              },
              {
                id: "lean" as const,
                label: m.night_table_margin,
                aria: m.night_table_lean,
              },
            ],
          },
        ]
      : []),
    ...(isPlural
      ? []
      : [
          {
            id: "base",
            label: m.night_table_group_base,
            columns: [
              {
                id: "base" as const,
                label: m.night_table_margin,
                aria: m.night_table_base,
              },
              {
                id: "shift" as const,
                label: m.night_table_shift,
                aria: m.night_table_shift,
              },
            ],
          },
        ]),
  ]);

  const place = (name: string) =>
    name.replace(/, [A-Z]{2}$/u, "").replace(/ County$/u, "");
  const ink = (value: number) => shade(value >= 0 ? a : b);
  const label = (index: number) => options[index]?.label ?? "";
  const shade = (index: number) =>
    `color-mix(in oklab, ${tone(options[index])} 80%, var(--foreground))`;

  function sort(next: Key) {
    if (key === next) {
      isAscending = !isAscending;
    } else {
      chosen = next;
      isAscending = next === "name";
    }
  }
</script>

{#snippet margin(value: number | null, isMuted: boolean)}
  {#if value == null}
    <span class="text-muted-foreground/60">–</span>
  {:else if Math.abs(value) < 0.0005}
    <span class="text-muted-foreground">{m.night_dial_tie()}</span>
  {:else}
    <span
      class={cn("font-figures tabular-nums", {
        "font-semibold": !isMuted,
        "opacity-75": isMuted,
      })}
      style:color={ink(value)}
    >
      <span class="sr-only">{surname(options[value >= 0 ? a : b])}</span>+{lead(
        value,
      )}
    </span>
  {/if}
{/snippet}

{#snippet bar(value: number | null)}
  <span
    class="relative hidden shrink-0 rounded-full bg-muted block-1.5 inline-14 md:block"
    aria-hidden="true"
  >
    <span class="absolute -inset-y-0.75 inset-s-1/2 bg-foreground/35 inline-px"
    ></span>
    {#if value != null}
      {const reach = $derived(Math.min(1, Math.abs(value) / 0.6) * 50)}
      <span
        class="absolute inset-y-0 rounded-full transition-all duration-700 motion-reduce:transition-none"
        style:left={value >= 0 ? `${50 - reach}%` : "50%"}
        style:width="{reach}%"
        style:background-color={tone(options[value >= 0 ? a : b])}
      ></span>
    {/if}
  </span>
{/snippet}

<section class="grid content-start gap-5" aria-labelledby="table-title">
  <header class="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
    <div class="grid gap-1.5">
      <h2 id="table-title" class="heading text-lg!">
        {isDistrict ? m.night_table_title_district() : m.night_table_title()}
      </h2>
      {#if answers != null}
        <ul
          class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
          aria-hidden="true"
        >
          {#each answers as index (options[index]?.id ?? index)}
            <li class="flex items-center gap-1.5">
              <span
                class="shrink-0 rounded-full block-2 inline-2"
                style:background-color={tone(options[index])}
              ></span>{options[index]?.label ?? ""}
            </li>
          {/each}
        </ul>
      {:else}
        <p
          class="flex items-center gap-2 text-xs text-muted-foreground"
          aria-hidden="true"
        >
          <span class="flex items-center gap-1.5"
            ><span
              class="rounded-full block-2 inline-2"
              style:background-color={tone(options[a])}
            ></span>{surname(options[a])}</span
          >
          <span
            class="relative rounded-full block-1.5 inline-10"
            style:background="linear-gradient(to right, {tone(options[a])} 0 50%,
            {tone(options[b])} 50% 100%)"
          ></span>
          <span class="flex items-center gap-1.5"
            >{surname(options[b])}<span
              class="rounded-full block-2 inline-2"
              style:background-color={tone(options[b])}
            ></span></span
          >
        </p>
      {/if}
    </div>
    <label class="relative flex items-center">
      <SearchIcon
        class="pointer-events-none absolute inset-s-3 text-muted-foreground block-4 inline-4"
        aria-hidden="true"
      />
      <input
        type="search"
        bind:value={query}
        placeholder={searchLabel}
        aria-label={searchLabel}
        class="rounded-sm border border-border bg-background ps-9 pe-3 text-sm block-9 inline-64 focus-visible:outline-2 focus-visible:outline-foreground"
      />
    </label>
  </header>
  <div class="relative overflow-x-auto">
    <table class="border-collapse text-sm inline-full">
      <thead>
        <tr>
          <td class="sticky inset-s-0 z-1 bg-background"></td>
          {#each groups as group (group.id)}
            <th
              scope="colgroup"
              colspan={group.columns.length}
              class="ps-5 pe-3 text-start align-bottom"
            >
              <span
                class="block border-be border-border pbe-1.5 text-xs font-semibold whitespace-nowrap"
                >{group.label()}</span
              >
            </th>
          {/each}
        </tr>
        <tr class="border-be border-foreground/80">
          <th
            scope="col"
            class="sticky inset-s-0 z-1 bg-background pbs-2 pbe-2 text-start align-bottom font-normal"
            aria-sort={key === "name"
              ? isAscending
                ? "ascending"
                : "descending"
              : "none"}
          >
            {@render header(
              { id: "name", label: placeLabel, aria: placeLabel },
              false,
            )}
          </th>
          {#each groups as group (group.id)}
            {#each group.columns as column, index (column.id)}
              <th
                scope="col"
                class={cn("pbs-2 pbe-2 text-end align-bottom font-normal", {
                  "ps-5": index === 0,
                  "ps-3": index > 0,
                })}
                aria-sort={key === column.id
                  ? isAscending
                    ? "ascending"
                    : "descending"
                  : "none"}
              >
                {@render header(column, true)}
              </th>
            {/each}
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each shown as entry (entry.row.unit.id)}
          {const row = $derived(entry.row)}
          {const isMarked = $derived(
            hovered === row.unit.id || selected === row.unit.id,
          )}
          {const isOpen = $derived(entry.left != null && entry.left >= 1)}
          {const link = $derived(href?.(row.unit.id) ?? null)}
          <tr
            class={cn("group border-be border-border transition-colors", {
              highlighter: isMarked,
            })}
            onpointerenter={(event) => {
              if (isHover(event)) hovered = row.unit.id;
            }}
            onpointerleave={() => (hovered = null)}
          >
            <th
              scope="row"
              class="sticky inset-s-0 z-1 bg-background py-3 ps-2 pe-6 text-start font-semibold group-[.highlighter]:bg-transparent"
            >
              <span class="flex items-center gap-2.5">
                <span
                  class="shrink-0 rounded-full block-2.5 inline-2.5"
                  style:background-color={row.leader == null
                    ? "var(--border)"
                    : tone(options[row.leader])}
                ></span>
                {#if link == null}
                  <span class="whitespace-nowrap" title={row.unit.name}
                    >{place(row.unit.name)}</span
                  >
                {:else}
                  <a
                    href={link}
                    class="whitespace-nowrap underline-offset-2 hover:underline"
                    title={row.unit.name}>{place(row.unit.name)}</a
                  >
                {/if}
              </span>
            </th>
            {#if answers != null}
              <td class="py-3 ps-5 pe-3">
                {#if row.leader == null || row.total === 0}
                  <span class="block text-end text-muted-foreground/60">–</span>
                {:else if row.margin < 0.0005}
                  <span class="block text-end text-muted-foreground"
                    >{m.night_dial_tie()}</span
                  >
                {:else}
                  {const name = $derived(label(row.leader))}
                  <span class="flex items-center justify-end gap-2.5">
                    <span
                      class="truncate text-xs text-muted-foreground max-inline-44 max-md:hidden"
                      title={name}>{name}</span
                    >
                    <span
                      class="text-end font-figures font-semibold tabular-nums min-inline-12"
                      style:color={shade(row.leader)}>+{lead(row.margin)}</span
                    >
                  </span>
                {/if}
              </td>
              <td class="p-3">
                {#if row.total > 0}
                  <span
                    class="ms-auto flex overflow-hidden rounded-full bg-muted block-2 inline-36"
                    role="img"
                    aria-label={answers
                      .map(
                        (index) =>
                          `${options[index]?.label ?? ""} ${percent((row.votes[index] ?? 0) / row.total)}`,
                      )
                      .join(", ")}
                  >
                    {#each answers as index (index)}
                      <span
                        class="border-e border-background block-full last:border-e-0"
                        style:inline-size="{((row.votes[index] ?? 0) /
                          row.total) *
                          100}%"
                        style:background-color={tone(options[index])}
                      ></span>
                    {/each}
                  </span>
                {:else}
                  <span class="block text-end text-muted-foreground/60">–</span>
                {/if}
              </td>
            {:else}
              <td class="py-3 ps-5 pe-3">
                <span class="flex items-center justify-end gap-3"
                  >{@render bar(row.signed)}<span class="text-end min-inline-12"
                    >{@render margin(row.signed, false)}</span
                  ></span
                >
              </td>
            {/if}
            <td class="p-3">
              <span class="flex items-center justify-end gap-2.5">
                <span
                  class="relative hidden overflow-hidden rounded-full bg-muted block-1.5 inline-12 md:block"
                  aria-hidden="true"
                >
                  <span
                    class="absolute inset-y-0 inset-s-0 rounded-full bg-foreground/75 transition-[inline-size] duration-700 motion-reduce:transition-none"
                    style:inline-size="{(entry.counted ?? 0) * 100}%"
                  ></span>
                </span>
                <span class="text-end font-figures tabular-nums min-inline-9"
                  >{entry.counted == null
                    ? "–"
                    : percent(entry.counted, 0)}</span
                >
              </span>
            </td>
            <td class="p-3 text-end font-figures tabular-nums"
              >{row.total > 0 ? count(row.total) : "–"}</td
            >
            {#if isLive}
              <td class="py-3 ps-5 pe-3 text-end font-figures tabular-nums"
                >{isOpen ? compact(entry.left ?? 0) : "–"}</td
              >
              <td class="p-3">
                <span class="flex items-center justify-end gap-3"
                  >{@render bar(isOpen ? entry.lean : null)}<span
                    class="text-end min-inline-12"
                    >{@render margin(isOpen ? entry.lean : null, false)}</span
                  ></span
                >
              </td>
            {/if}
            {#if answers == null}
              <td class="py-3 ps-5 pe-3 text-end"
                >{@render margin(row.baseSigned, true)}</td
              >
              <td class="py-3 ps-3 pe-2 text-end">
                {#if entry.shift == null || Math.abs(entry.shift) < 0.0005}
                  <span class="text-muted-foreground/60">–</span>
                {:else}
                  <span
                    class="inline-flex items-center gap-1 font-figures tabular-nums"
                    style:color={ink(entry.shift)}
                  >
                    {#if entry.shift >= 0}
                      <ArrowRightIcon
                        class="block-3.5 inline-3.5"
                        aria-hidden="true"
                      />
                    {:else}
                      <ArrowLeftIcon
                        class="block-3.5 inline-3.5"
                        aria-hidden="true"
                      />
                    {/if}
                    <span class="sr-only"
                      >{surname(options[entry.shift >= 0 ? a : b])}</span
                    >{lead(entry.shift)}
                  </span>
                {/if}
              </td>
            {/if}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#if query.trim() === "" && sorted.length > LIMIT}
    <button
      type="button"
      class="-mbs-5 flex items-center justify-center gap-1.5 border-be border-border py-3 text-sm font-semibold text-muted-foreground hover:highlighter hover:text-foreground"
      aria-expanded={isAll}
      onclick={() => (isAll = !isAll)}
    >
      {isAll
        ? m.night_table_fewer()
        : isDistrict
          ? m.night_table_all_district({ count: sorted.length })
          : m.night_table_all({ count: sorted.length })}
      <ChevronDownIcon
        class={cn("transition-transform block-4 inline-4", {
          "rotate-180": isAll,
        })}
        aria-hidden="true"
      />
    </button>
  {/if}
</section>

{#snippet header(column: Column, isNumeric: boolean)}
  <button
    type="button"
    class={cn(
      "inline-flex items-center gap-1 rounded-xs px-1 py-0.5 text-xs whitespace-nowrap hover:text-foreground",
      {
        "flex-row-reverse": isNumeric,
        "font-semibold text-foreground": key === column.id,
        "text-muted-foreground": key !== column.id,
      },
    )}
    aria-label={column.aria()}
    onclick={() => sort(column.id)}
  >
    {column.label()}
    <span class="block-3 inline-3">
      {#if key === column.id}
        {#if isAscending}
          <ArrowUpIcon class="block-3 inline-3" aria-hidden="true" />
        {:else}
          <ArrowDownIcon class="block-3 inline-3" aria-hidden="true" />
        {/if}
      {/if}
    </span>
  </button>
{/snippet}
