<script lang="ts">
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import { createQuery } from "@tanstack/svelte-query";
  import Name from "#lib/components/name.svelte";
  import { Input } from "#lib/components/ui/input/index.js";
  import { count, percent, plural, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import Preview from "./preview.svelte";

  let {
    id,
    unit,
    level,
    noun,
    parents,
    match,
    group,
    round,
    isMapped,
    focus = $bindable(null),
  }: {
    id: string;
    unit: string | undefined;
    level: string | null;
    noun: string;
    parents: Array<{ k: number; name: string; path: string | null }>;
    match: string;
    group: string | undefined;
    round: number;
    isMapped: boolean;
    focus?: number | null;
  } = $props();

  const ledger = createQuery(() => ({
    ...orpc.contests.ledger.queryOptions({
      input: {
        id,
        unit,
        level: level ?? undefined,
        match: match === "person" || match === "family" ? match : "party",
        group,
        round,
      },
    }),
    staleTime: Infinity,
  }));

  const uid = $props.id();
  let query = $state("");
  let all = $state(false);
  let opened = $state<Array<number | null>>([]);
  let full = $state<Array<number | null>>([]);
  let closed = $state<Array<number | null>>([]);

  const places = $derived(
    ledger.data?.isCapped === false ? ledger.data.places : null,
  );
  const legend = $derived(places?.legend ?? []);
  const titles = $derived(
    legend.map((item) =>
      item.person == null ? (item.short ?? item.label) : item.label,
    ),
  );
  const colors = $derived(legend.map((item) => swatch(item.color, item.key)));
  const nouns = $derived(
    new Intl.ListFormat(getLocale(), {
      style: "long",
      type: "conjunction",
    }).format((places?.levels ?? []).map((entry) => plural(entry.noun))),
  );
  const titled = $derived(
    new Intl.ListFormat(getLocale(), {
      style: "long",
      type: "conjunction",
    }).format((places?.levels ?? []).map((entry) => entry.noun)),
  );
  const known = $derived(new Map(parents.map((row) => [row.k, row])));
  const shown = $derived.by(() => {
    if (places == null) {
      return [];
    }
    const groups = ledger.data?.groups ?? [];
    const isMixed = groups.some((entry) => entry.k != null);
    let at = 0;
    return groups.map((entry) => {
      const units = places.rows.slice(at, at + entry.size);
      at += entry.size;
      const parent = entry.k == null ? null : known.get(entry.k);
      return {
        k: entry.k,
        name:
          parent?.name ??
          (isMixed && entry.k == null
            ? m.ledger_unplaced({ place: entry.name, group: noun })
            : entry.name),
        path: parent?.path ?? null,
        valid: entry.valid,
        units,
      };
    });
  });
  const echoes = $derived(ledger.data?.folded ?? 0);
  const isFlat = $derived(shown.every((entry) => entry.k == null));
  const normalized = (text: string) =>
    text
      .toLocaleLowerCase(getLocale())
      .normalize("NFKD")
      .replaceAll(/\p{M}/gu, "");
  const matched = $derived.by(() => {
    const needle = normalized(query.trim());
    if (needle === "") {
      return shown;
    }
    return shown.flatMap((entry) => {
      if (normalized(entry.name).includes(needle)) {
        return [entry];
      }
      const units = entry.units.filter((row) =>
        normalized(row.name).includes(needle),
      );
      return units.length === 0 ? [] : [{ ...entry, units }];
    });
  });
  const LIMIT = 24;
  const visible = $derived(
    all || query !== "" ? matched : matched.slice(0, LIMIT),
  );
  const isOpen = (k: number | null) =>
    isFlat ||
    query.trim() !== "" ||
    opened.includes(k) ||
    (shown.length <= 6 && !closed.includes(k));
  const leads = $derived(
    Map.groupBy(
      shown.flatMap((entry) => entry.units).filter((row) => row.leader != null),
      (row) => row.leader ?? 0,
    )
      .entries()
      .map(([index, rows]) => [index, rows.length] as const)
      .toArray()
      .toSorted((a, b) => b[1] - a[1]),
  );
</script>

{#if places != null && shown.length > 0}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="{uid}-title"
  >
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2.5">
      <h2 id="{uid}-title" class="heading">
        {m.places_title({ noun: titled })}
      </h2>
      {#if shown.length > 8 || (ledger.data?.count ?? 0) > 20}
        <Input
          class="text-sm block-8 inline-full sm:ms-auto sm:inline-56"
          type="search"
          placeholder={m.places_filter()}
          aria-label={m.places_filter()}
          bind:value={query}
        />
      {/if}
    </div>
    <p
      class="-mbs-1.5 text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
    >
      {isFlat
        ? m.ledger_lede_flat({ nouns })
        : isMapped
          ? m.ledger_lede_mapped({ nouns, group: noun })
          : m.ledger_lede({ nouns, group: noun })}
      {#if echoes > 0}
        {m.ledger_single({
          count: count(echoes),
          groups: echoes === 1 ? noun : plural(noun),
        })}
      {/if}
    </p>
    {#if leads.length > 1}
      <ul class="flex flex-wrap gap-x-3 gap-y-1 text-[0.8125rem]">
        {#each leads as [index, won] (index)}
          <li class="flex items-center gap-1.5">
            <span
              class="rounded-xs bg-(--party) block-2.5 inline-2.5"
              style:--party={colors[index]}
            ></span>
            <span class="font-semibold">{titles[index]}</span>
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              >{count(won)}</span
            >
          </li>
        {/each}
      </ul>
    {/if}
    {#if query.trim() !== "" && matched.length === 0}
      <p class="text-[0.8125rem] text-muted-foreground">
        {m.places_filter_empty({ query: query.trim() })}
      </p>
    {/if}

    <ul class="grid border-bs border-border">
      {#each visible as entry (entry.k ?? "direct")}
        {const open = $derived(isOpen(entry.k))}
        <li
          class={cn("border-be border-border/60", {
            "bg-secondary/40": focus != null && focus === entry.k,
          })}
          onpointerenter={() => (focus = entry.k)}
          onpointerleave={() => (focus = null)}
        >
          {#if !isFlat}
            <button
              type="button"
              class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 px-2 py-2 text-start inline-full hover:bg-secondary/70 sm:grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)_auto]"
              aria-expanded={open}
              aria-controls="{uid}-{entry.k ?? 'direct'}"
              onfocus={() => (focus = entry.k)}
              onblur={() => (focus = null)}
              onclick={() => {
                if (open) {
                  opened = opened.filter((k) => k !== entry.k);
                  closed = [...closed, entry.k];
                } else {
                  closed = closed.filter((k) => k !== entry.k);
                  opened = [...opened, entry.k];
                }
              }}
            >
              <span class="flex items-baseline gap-2 min-inline-0">
                <span class="truncate text-sm font-semibold">{entry.name}</span>
                <span
                  class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
                  >{count(entry.units.length)}</span
                >
              </span>
              <span class="flex items-center gap-3 justify-self-end">
                <span
                  class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums max-sm:hidden"
                  >{count(entry.valid)}</span
                >
                <ChevronDownIcon
                  class={cn(
                    "text-muted-foreground transition-transform block-4 inline-4 motion-reduce:transition-none",
                    { "rotate-180": open },
                  )}
                />
              </span>
              <span
                class="col-span-2 flex flex-wrap gap-0.5 sm:col-span-1 sm:col-start-2 sm:row-start-1"
                aria-hidden="true"
              >
                {#each entry.units as row (row.k)}
                  <span
                    class={cn("rounded-[1px] block-2.5 inline-2.5", {
                      "bg-(--party)": row.leader != null,
                      "border border-muted-foreground/50": row.leader == null,
                    })}
                    style:--party={row.leader == null
                      ? undefined
                      : colors[row.leader]}
                  ></span>
                {/each}
              </span>
            </button>
          {/if}
          {#if open}
            <div id="{uid}-{entry.k ?? 'direct'}" class="pbe-2">
              <table class="border-collapse text-sm/tight inline-full">
                <tbody>
                  {#each full.includes(entry.k) || entry.units.length <= 15 ? entry.units : entry.units.slice(0, 12) as row (row.k)}
                    <tr
                      class="border-bs border-border/40 first:border-bs-0 hover:bg-secondary/70"
                    >
                      <th
                        class={cn("py-1.5 pe-2 text-start font-normal", {
                          "ps-5": !isFlat,
                          "ps-2": isFlat,
                        })}
                        scope="row"
                      >
                        <span class="font-medium">{row.name}</span>
                        <span
                          class="mbs-1.5 flex overflow-hidden rounded-xs bg-muted block-2 sm:hidden"
                          aria-hidden="true"
                        >
                          {#each row.shares as share, index (index)}
                            {#if share > 0}
                              <span
                                class="shrink-0 bg-(--party) not-last:border-e not-last:border-card"
                                style:--party={colors[index]}
                                style:inline-size="{share * 100}%"
                              ></span>
                            {/if}
                          {/each}
                        </span>
                      </th>
                      <td class="px-2 py-1.5 inline-[34%] max-sm:hidden">
                        <span
                          class="flex overflow-hidden rounded-xs bg-muted block-3"
                          aria-hidden="true"
                        >
                          {#each row.shares as share, index (index)}
                            {#if share > 0}
                              <span
                                class="shrink-0 bg-(--party) not-last:border-e not-last:border-card"
                                style:--party={colors[index]}
                                style:inline-size="{share * 100}%"
                              ></span>
                            {/if}
                          {/each}
                        </span>
                      </td>
                      <td class="px-2 py-1.5 whitespace-nowrap">
                        {#if row.leader != null}
                          <span class="flex items-center gap-1.5">
                            <Name
                              class="truncate font-semibold max-inline-40"
                              label={titles[row.leader] ?? ""}
                              color={colors[row.leader]}
                              person={legend[row.leader]?.person}
                              party={legend[row.leader]?.party}
                            />
                            <span
                              class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                              >{percent(row.shares[row.leader] ?? 0)}</span
                            >
                          </span>
                        {:else if row.valid === 0}
                          <span class="text-[0.8125rem] text-muted-foreground"
                            >{m.places_no_votes()}</span
                          >
                        {/if}
                      </td>
                      <td
                        class="px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap tabular-nums max-sm:hidden"
                      >
                        {row.margin == null ? "" : points(row.margin)}
                      </td>
                      <td
                        class="py-1.5 ps-2 pe-2 text-end font-figures text-[0.8125rem] whitespace-nowrap tabular-nums"
                      >
                        {count(row.valid)}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
              {#if !full.includes(entry.k) && entry.units.length > 15}
                <button
                  type="button"
                  class="mis-5 mbs-1.5 rounded-sm border border-input bg-card px-3 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary"
                  onclick={() => (full = [...full, entry.k])}
                  >{m.places_show_all({
                    count: count(entry.units.length),
                  })}</button
                >
              {/if}
              {#if entry.path != null}
                {const target = $derived(entry.path)}
                <p class="ps-5 pbs-1 text-[0.8125rem]">
                  <Preview
                    class="text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground"
                    path={target}>{m.ledger_open({ name: entry.name })}</Preview
                  >
                </p>
              {/if}
            </div>
          {/if}
        </li>
      {/each}
    </ul>
    {#if !all && query === "" && matched.length > LIMIT}
      <button
        type="button"
        class="justify-self-start rounded-sm border border-input bg-card px-3 text-[0.8125rem] font-semibold keycap block-8 hover:bg-secondary"
        onclick={() => (all = true)}
        >{m.places_show_all({ count: count(matched.length) })}</button
      >
    {/if}
  </section>
{/if}
