<script lang="ts">
  import type { LiveOption } from "@ea/analytics/live";
  import SearchIcon from "@lucide/svelte/icons/search";
  import type { UnitStanding } from "#lib/live/standing.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { percent } from "#lib/format.js";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  type Sort = "votes" | "margin" | "shift" | "name";

  const {
    rows,
    isDistrict = false,
    options,
    pair,
    selected,
    onPick,
    onHover,
  }: {
    isDistrict?: boolean;
    rows: Array<{
      areaId: string;
      name: string;
      row: UnitStanding;
      counted: number | null;
      color: string;
    }>;
    options: Array<LiveOption>;
    pair: [number, number];
    selected: string | null;
    onPick: (areaId: string) => void;
    onHover: (areaId: string | null) => void;
  } = $props();

  let query = $state("");
  let sort = $state<Sort>("votes");
  let isAll = $state(false);

  const searchLabel = $derived(
    isDistrict ? m.night_table_search_district() : m.night_table_search(),
  );
  const SORTS = $derived([
    { value: "votes" as const, label: m.night_table_votes() },
    { value: "margin" as const, label: m.night_table_margin() },
    { value: "shift" as const, label: m.night_table_shift() },
    {
      value: "name" as const,
      label: isDistrict ? m.night_table_district() : m.night_table_county(),
    },
  ]);

  function shiftOf(row: UnitStanding) {
    return row.signed == null || row.baseSigned == null
      ? null
      : row.signed - row.baseSigned;
  }

  function weight(row: UnitStanding) {
    return row.total > 0 ? row.total : (row.baseline?.turnout ?? 0);
  }

  const matched = $derived.by(() => {
    const needle = query.trim().toLocaleLowerCase();
    const kept =
      needle === ""
        ? rows
        : rows.filter((entry) =>
            entry.name.toLocaleLowerCase().includes(needle),
          );
    return kept.toSorted((a, b) => {
      if (sort === "name") {
        return a.name.localeCompare(b.name);
      }
      if (sort === "margin") {
        const left = a.row.total > 0 ? a.row.margin : Infinity;
        const right = b.row.total > 0 ? b.row.margin : Infinity;
        return left - right || weight(b.row) - weight(a.row);
      }
      if (sort === "shift") {
        return (
          Math.abs(shiftOf(b.row) ?? -1) - Math.abs(shiftOf(a.row) ?? -1) ||
          weight(b.row) - weight(a.row)
        );
      }
      return weight(b.row) - weight(a.row);
    });
  });
  const visible = $derived(
    isAll || query.trim() !== "" ? matched : matched.slice(0, 12),
  );
  const hasVotes = $derived(rows.some((entry) => entry.row.total > 0));
</script>

<section
  class="grid gap-2.5"
  aria-label={isDistrict
    ? m.night_table_title_district()
    : m.night_table_title()}
>
  <div class="flex items-center justify-between gap-3">
    <h3 class="text-xs font-semibold text-muted-foreground">
      {isDistrict ? m.night_table_title_district() : m.night_table_title()}
    </h3>
  </div>
  <label class="relative block">
    <span class="sr-only">{searchLabel}</span>
    <SearchIcon
      class="pointer-events-none absolute inset-s-2.5 inset-bs-1/2 -translate-y-1/2 text-muted-foreground block-3.5 inline-3.5"
      aria-hidden="true"
    />
    <input
      type="search"
      bind:value={query}
      placeholder={searchLabel}
      class="rounded-sm bg-background ps-8 pe-2 text-sm ring-1 ring-input outline-none block-9 inline-full placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
    />
  </label>
  <Segmented
    options={SORTS}
    bind:value={sort}
    label={isDistrict ? m.livemap_sort_district() : m.livemap_sort()}
    size="sm"
    fill
  />
  <ul class="grid">
    {#each visible as entry (entry.areaId)}
      {const row = $derived(entry.row)}
      {const shift = $derived(shiftOf(row))}
      {const leader = $derived<LiveOption | undefined>(
        row.leader == null ? undefined : options[row.leader],
      )}
      {const toward = $derived<LiveOption | undefined>(
        shift == null ? undefined : options[shift >= 0 ? pair[0] : pair[1]],
      )}
      <li>
        <button
          type="button"
          class={cn(
            "-mx-2 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2.5 rounded-sm px-2 py-1.5 text-start inline-[calc(100%+1rem)] hover:bg-secondary focus-visible:bg-secondary",
            { "bg-secondary": selected === entry.areaId },
          )}
          onclick={() => onPick(entry.areaId)}
          onpointerenter={() => onHover(entry.areaId)}
          onpointerleave={() => onHover(null)}
          onfocus={() => onHover(entry.areaId)}
          onblur={() => onHover(null)}
        >
          <span
            class="rounded-xs ring-1 ring-border/60 block-3.5 inline-3.5"
            style:background-color={entry.color}
          ></span>
          <span class="grid min-inline-0">
            <span class="truncate text-[0.8125rem] font-semibold"
              >{entry.name}</span
            >
            <span
              class="truncate font-figures text-xs text-muted-foreground tabular-nums"
            >
              {#if leader != null}
                <span style:color={tone(leader)} class="font-semibold"
                  >{surname(leader)} +{lead(row.margin)}</span
                >
              {:else if row.baseSigned != null}
                {m.night_tip_baseline({
                  margin: `${surname(options[row.baseSigned >= 0 ? pair[0] : pair[1]])} +${lead(Math.abs(row.baseSigned))}`,
                })}
              {:else}
                {m.night_tip_waiting()}
              {/if}
            </span>
          </span>
          <span
            class="grid justify-items-end font-figures text-xs tabular-nums"
          >
            {#if entry.counted != null && row.total > 0}
              <span class="text-muted-foreground"
                >{percent(entry.counted, 0)}</span
              >
            {/if}
            {#if hasVotes && shift != null && toward != null}
              <span
                class="font-semibold"
                style:color={tone(toward)}
                title={m.livemap_shift({
                  points: lead(Math.abs(shift)),
                  name: surname(toward),
                })}>+{lead(Math.abs(shift))}</span
              >
            {/if}
          </span>
        </button>
      </li>
    {/each}
  </ul>
  {#if query.trim() === "" && matched.length > 12}
    <button
      type="button"
      class="justify-self-start rounded-sm px-2 text-[0.8125rem] font-semibold underline decoration-border underline-offset-3 block-8 hover:decoration-foreground"
      onclick={() => (isAll = !isAll)}
      >{isAll
        ? m.night_table_fewer()
        : isDistrict
          ? m.night_table_all_district({ count: matched.length })
          : m.night_table_all({ count: matched.length })}</button
    >
  {/if}
</section>
