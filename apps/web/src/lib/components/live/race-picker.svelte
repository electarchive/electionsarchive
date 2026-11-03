<script lang="ts">
  import type { LiveRace } from "@ea/analytics/live";
  import type { Snippet } from "svelte";
  import CheckIcon from "@lucide/svelte/icons/check";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import SearchIcon from "@lucide/svelte/icons/search";
  import XIcon from "@lucide/svelte/icons/x";
  import type { Summary } from "#lib/live/summary.js";
  import { isHover } from "#lib/explain.svelte.js";
  import { percent } from "#lib/format.js";
  import { HATCH, surname, tone } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import { localize, tokenize } from "#lib/live/local.js";
  import { shade, tally, verdict } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    summaries,
    current = null,
    label,
    overview = null,
    onpick = null,
    isCompact = false,
  }: {
    summaries: Array<Summary>;
    current?: string | null;
    label: string;
    overview?: { label: string; isCurrent: boolean; onpick: () => void } | null;
    onpick?: ((summary: Summary) => void) | null;
    isCompact?: boolean;
  } = $props();

  const id = $props.id();

  const toLive = liveLinks();

  const KINDS = [
    { value: "statewide", label: m.night_index_statewide },
    { value: "house", label: m.night_nation_house },
    { value: "legislature", label: m.night_state_legislature },
    { value: "measure", label: m.night_local_measures },
    { value: "judicial", label: m.night_local_judicial },
    { value: "county", label: m.night_local_county },
    { value: "city", label: m.night_local_city },
    { value: "school", label: m.night_local_school },
    { value: "special", label: m.night_local_special },
    { value: "other", label: m.night_local_other },
  ];
  const JUDICIAL = /\b(?:judge|justice|court|magistrate|surrogate)\b/iu;
  const LEGISLATURE =
    /\b(?:house|senate|assembly|legislat\w*|delegates?)\b.+\bdistrict\b/iu;
  const RANK: Partial<Record<LiveRace["office"], number>> = {
    senate: 0,
    governor: 1,
  };

  let tab = $state<string | null>(null);
  let isAll = $state(false);
  let query = $state("");
  let width = $state(0);
  let probed = $state<string | null>(null);
  let openFor = $state<string | null>(null);

  const isFolded = $derived(isCompact && openFor !== (current ?? ""));

  const place = $derived.by(() => {
    const titles = summaries
      .filter((summary) => summary.race.office === "other")
      .map((summary) => summary.race.title.split(" "));
    const names = summaries.flatMap((summary) =>
      summary.race.jurisdiction == null ? [] : [summary.race.jurisdiction.name],
    );
    let best = "";
    for (let size = 1; size <= 5; size += 1) {
      const counts = new Map<string, number>();
      for (const words of titles) {
        if (words.length > size) {
          const key = words.slice(0, size).join(" ");
          counts.set(key, (counts.get(key) ?? 0) + 1);
        }
      }
      let top = "";
      let most = 0;
      for (const [key, found] of counts) {
        if (found > most) {
          top = key;
          most = found;
        }
      }
      if (most < 2 || most * 2 < titles.length) {
        break;
      }
      if (names.some((name) => name.endsWith(top))) {
        best = top;
      }
    }
    return best;
  });

  function kindOf(summary: Summary) {
    const { race } = summary;
    if (race.office === "house") {
      return "house";
    }
    if (race.office === "senate" || race.office === "governor") {
      return "statewide";
    }
    const local = localize(summary, race.state);
    if (local.isMeasure) {
      return "measure";
    }
    if (JUDICIAL.test(race.title)) {
      return "judicial";
    }
    if (LEGISLATURE.test(race.title)) {
      return "legislature";
    }
    if (race.jurisdiction?.kind === "state") {
      return local.kind === "city" || local.kind === "school"
        ? local.kind
        : "statewide";
    }
    return local.kind;
  }

  function title(race: LiveRace) {
    if (race.office === "senate") {
      return m.night_nation_senate();
    }
    if (race.office === "governor") {
      return m.night_nation_governor();
    }
    if (race.office === "house") {
      return race.district == null || race.district === "0"
        ? m.night_nation_house()
        : m.night_rail_house({ district: race.district });
    }
    const rest =
      place !== "" && race.title.startsWith(`${place} `)
        ? race.title.slice(place.length + 1)
        : race.title;
    return rest.charAt(0).toUpperCase() + rest.slice(1);
  }

  function shared(titles: Array<string>) {
    let prefix = titles[0] ?? "";
    for (const entry of titles) {
      while (!entry.startsWith(prefix)) {
        prefix = prefix.slice(0, -1);
      }
    }
    const cut = Math.max(prefix.lastIndexOf(": "), prefix.lastIndexOf(", "));
    return cut === -1 ? "" : prefix.slice(0, cut + 2);
  }

  const groups = $derived.by(() => {
    const found = new Map<string, Array<Summary>>();
    for (const summary of summaries) {
      const kind = kindOf(summary);
      const list = found.get(kind) ?? [];
      list.push(summary);
      found.set(kind, list);
    }
    return KINDS.flatMap((kind) => {
      const list = found.get(kind.value);
      if (list == null) {
        return [];
      }
      const sorted =
        kind.value === "house"
          ? list.toSorted(
              (a, b) =>
                Number.parseInt(a.race.district ?? "0", 10) -
                Number.parseInt(b.race.district ?? "0", 10),
            )
          : list.toSorted(
              (a, b) =>
                (RANK[a.race.office] ?? 2) - (RANK[b.race.office] ?? 2) ||
                title(a.race).localeCompare(title(b.race), undefined, {
                  numeric: true,
                }),
            );
      const names = sorted.map((summary) => title(summary.race));
      const prefix =
        kind.value === "statewide" || kind.value === "house" || list.length < 2
          ? ""
          : shared(names);
      return [
        {
          key: kind.value,
          label: kind.label(),
          prefix: prefix.replace(/[:,]\s*$/u, ""),
          entries: sorted.map((summary, index) => ({
            summary,
            name: (names[index] ?? "").slice(prefix.length),
            haystack: localize(summary, summary.race.state).haystack,
          })),
        },
      ];
    });
  });

  const total = $derived(summaries.length);
  const tabs = $derived([
    { key: "all", label: m.night_local_all(), count: total },
    ...groups.map((group) => ({
      key: group.key,
      label: group.label,
      count: group.entries.length,
    })),
  ]);
  const columns = $derived(Math.max(1, Math.floor(width / 272)));
  const limit = $derived(columns === 1 ? 6 : columns * 3);
  const isLong = $derived(
    groups.some((group) => group.key !== "house" && group.entries.length > 1) &&
      total > limit,
  );
  const home = $derived(
    groups.find((group) =>
      group.entries.some((entry) => entry.summary.race.slug === current),
    )?.key ??
      (overview?.isCurrent === true ? "house" : null) ??
      groups[0]?.key ??
      "all",
  );
  const chosen = $derived(
    groups.length < 2 ? "all" : (tab ?? (isLong ? home : "all")),
  );
  const tokens = $derived(tokenize(query));
  const isSearching = $derived(tokens.length > 0);
  const shown = $derived(
    isSearching
      ? groups.flatMap((group) => {
          const entries = group.entries.filter((entry) =>
            tokens.every((token) => entry.haystack.includes(token)),
          );
          return entries.length === 0 ? [] : [{ ...group, entries }];
        })
      : groups.filter((group) => chosen === "all" || group.key === chosen),
  );
  const counts = $derived.by(() => {
    let called = 0;
    let counting = 0;
    let later = 0;
    for (const summary of summaries) {
      if (summary.winner != null || summary.projected != null) {
        called += 1;
      } else if (summary.leader != null) {
        counting += 1;
      } else if (summary.later != null) {
        later += 1;
      }
    }
    return {
      called,
      counting,
      later,
      waiting: total - called - counting - later,
    };
  });
  const probe = $derived(
    summaries.find((summary) => summary.race.slug === probed) ?? null,
  );

  function clip<T extends { summary: Summary }>(entries: Array<T>) {
    if (
      isAll ||
      isSearching ||
      chosen === "all" ||
      entries.length <= limit + 1
    ) {
      return entries;
    }
    const at = entries.findIndex(
      (entry) => entry.summary.race.slug === current,
    );
    const head = entries.slice(0, at >= limit ? limit - 1 : limit);
    const found = entries[at];
    return at >= limit && found != null ? [...head, found] : head;
  }

  function choose(key: string) {
    tab = key;
    isAll = false;
    query = "";
  }

  function pick(summary: Summary) {
    probed = null;
    onpick?.(summary);
  }
</script>

{#snippet swatch(fill: string, image: string | null = null)}
  <span
    class="inline-block shrink-0 rounded-[1px] block-2 inline-2"
    style:background-color={fill}
    style:background-image={image}
    aria-hidden="true"
  ></span>
{/snippet}

{#snippet target(
  summary: Summary,
  className: string,
  aria: string | null,
  style: string | null,
  body: Snippet,
)}
  {const isCurrent = $derived(summary.race.slug === current)}
  {#if onpick == null}
    <a
      href={toLive(summary.race.slug)}
      aria-current={isCurrent ? "page" : undefined}
      aria-label={aria}
      class={className}
      {style}
      onpointerenter={(event) => {
        if (isHover(event)) probed = summary.race.slug;
      }}
      onfocus={() => (probed = summary.race.slug)}
      onblur={() => (probed = null)}>{@render body()}</a
    >
  {:else}
    <button
      type="button"
      aria-pressed={isCurrent}
      aria-label={aria}
      class={className}
      {style}
      onclick={() => pick(summary)}
      onpointerenter={(event) => {
        if (isHover(event)) probed = summary.race.slug;
      }}
      onfocus={() => (probed = summary.race.slug)}
      onblur={() => (probed = null)}>{@render body()}</button
    >
  {/if}
{/snippet}

{#snippet row(entry: { summary: Summary; name: string })}
  {const summary = $derived(entry.summary)}
  {const option = $derived(summary.winner ?? summary.leader)}
  {const isCurrent = $derived(summary.race.slug === current)}
  {const isCalled = $derived(summary.winner != null)}
  <li class="border-be border-border">
    {#snippet body()}
      <span
        class={cn("mbs-1.5 shrink-0 rounded-full block-2.5 inline-2.5", {
          "ring-1 ring-muted-foreground/50 ring-inset": option == null,
        })}
        style:background-color={option == null ? undefined : tone(option)}
        style:background-image={summary.later == null ? undefined : HATCH}
        aria-hidden="true"
      ></span>
      <span class="grid gap-0.5 min-inline-0">
        <span class="flex items-baseline justify-between gap-3">
          {const cut = $derived(entry.name.indexOf(": "))}
          <span
            class="truncate text-[0.8125rem] leading-snug font-semibold min-inline-0"
            title={summary.race.title}
            >{#if cut === -1}{entry.name}{:else}{entry.name.slice(0, cut)}<span
                class="font-normal text-muted-foreground"
              >
                {entry.name.slice(cut + 2)}</span
              >{/if}</span
          >
          {#if summary.counted != null && summary.later == null}
            <span
              class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
              >{percent(summary.counted, 0)}</span
            >
          {/if}
        </span>
        <span class="flex items-center justify-between gap-3">
          <span
            class={cn("flex items-center gap-1 truncate text-xs", {
              "font-semibold text-foreground": isCalled,
              "text-muted-foreground": !isCalled,
            })}
            >{#if isCalled}<CheckIcon
                class="shrink-0 block-3 inline-3"
                aria-hidden="true"
              />{/if}<span class="truncate">{verdict(summary)}</span></span
          >
          {#if summary.counted != null && summary.later == null}
            <span
              class="relative shrink-0 overflow-hidden rounded-xs bg-muted block-1 inline-10"
              aria-hidden="true"
            >
              <span
                class="absolute inset-y-0 inset-s-0 bg-foreground transition-[inline-size] duration-700 motion-reduce:transition-none"
                style:inline-size="{summary.counted * 100}%"
              ></span>
            </span>
          {/if}
        </span>
      </span>
    {/snippet}
    {@render target(
      summary,
      cn(
        "-mbe-px flex inline-full items-start gap-2.5 border-s-2 py-2 ps-2 pe-2.5 text-start outline-none transition-colors focus-visible:bg-secondary focus-visible:ring-2 focus-visible:ring-ring/50 pointer-coarse:py-2.5",
        {
          "border-foreground bg-secondary": isCurrent,
          "border-transparent hover:bg-secondary": !isCurrent,
        },
      ),
      null,
      null,
      body,
    )}
  </li>
{/snippet}

{#snippet house(entries: Array<{ summary: Summary; name: string }>)}
  {const seats = $derived(
    tally(
      entries.map((entry) => entry.summary),
      "house",
    ),
  )}
  {const focus = $derived(
    probe != null && probe.race.office === "house"
      ? probe
      : (entries.find((entry) => entry.summary.race.slug === current)
          ?.summary ?? null),
  )}
  <div class="grid gap-2 pbs-1">
    <p
      class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs min-block-5"
      aria-live="polite"
    >
      {#if focus == null}
        {#each seats.groups as group (group.option.id)}
          <span class="flex items-center gap-1.5 font-semibold">
            {@render swatch(tone(group.option))}
            {group.option.party?.short ?? surname(group.option)}
            <span class="font-figures tabular-nums"
              >{group.won + group.leading}</span
            >
          </span>
        {/each}
        {#if seats.waiting > 0}
          <span class="flex items-center gap-1.5 text-muted-foreground">
            {@render swatch("var(--muted)")}
            {m.night_races_waiting({ count: seats.waiting })}
          </span>
        {/if}
        {#if seats.later > 0}
          <span class="flex items-center gap-1.5 text-muted-foreground">
            {@render swatch("var(--muted)", HATCH)}
            {m.night_races_later({ count: seats.later })}
          </span>
        {/if}
      {:else}
        <span class="font-semibold">{title(focus.race)}</span>
        <span class="text-muted-foreground">{verdict(focus)}</span>
        {#if focus.counted != null}
          <span class="font-figures text-muted-foreground tabular-nums"
            >{m.night_races_counted({ share: percent(focus.counted, 0) })}</span
          >
        {/if}
      {/if}
    </p>
    <ol
      class="grid grid-cols-[repeat(auto-fill,minmax(2.25rem,1fr))] gap-1"
      onpointerleave={() => (probed = null)}
    >
      {#if overview != null}
        <li class="col-span-3">
          <button
            type="button"
            aria-pressed={overview.isCurrent}
            class={cn(
              "grid place-items-center rounded-xs px-2 text-xs font-semibold block-8 inline-full pointer-coarse:block-10",
              {
                "bg-foreground text-background": overview.isCurrent,
                "ring-1 ring-border hover:bg-secondary": !overview.isCurrent,
              },
            )}
            onclick={overview.onpick}>{overview.label}</button
          >
        </li>
      {/if}
      {#each entries as entry (entry.summary.race.id)}
        {const summary = $derived(entry.summary)}
        {const color = $derived(shade(summary))}
        {const isCurrent = $derived(summary.race.slug === current)}
        <li>
          {#snippet body()}
            {summary.race.district === "0"
              ? "AL"
              : (summary.race.district ?? "")}
          {/snippet}
          {@render target(
            summary,
            cn(
              "grid inline-full place-items-center rounded-xs font-figures text-xs font-semibold tabular-nums transition-[background-color,box-shadow] duration-500 outline-none block-8 focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none pointer-coarse:block-10",
              {
                "text-muted-foreground": color == null,
                "text-white": summary.winner != null,
              },
              {
                "ring-2 ring-foreground ring-offset-2 ring-offset-background":
                  isCurrent,
                "hover:ring-1 hover:ring-foreground": !isCurrent,
              },
            ),
            `${title(summary.race)}: ${verdict(summary)}`,
            `background-color: ${color ?? "var(--muted)"}; background-image: ${summary.later == null ? "none" : HATCH}`,
            body,
          )}
        </li>
      {/each}
    </ol>
  </div>
{/snippet}

<nav
  aria-label={label}
  class="grid grid-cols-[minmax(0,1fr)] gap-3"
  bind:clientWidth={width}
>
  <div class="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
    <div class="grid gap-1.5 min-inline-0">
      <p class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span class="text-[0.9375rem] font-bold"
          >{m.night_picker_races({ count: total })}</span
        >
        <span
          class="flex flex-wrap items-center gap-x-3 gap-y-0.5 font-figures text-xs text-muted-foreground tabular-nums"
        >
          {#if counts.called > 0}
            <span class="flex items-center gap-1.5"
              >{@render swatch("var(--foreground)")}{m.night_index_called({
                count: counts.called,
              })}</span
            >
          {/if}
          {#if counts.counting > 0}
            <span class="flex items-center gap-1.5"
              >{@render swatch(
                "color-mix(in oklab, var(--foreground) 40%, var(--background))",
              )}{m.night_index_counting({ count: counts.counting })}</span
            >
          {/if}
          {#if counts.waiting > 0}
            <span class="flex items-center gap-1.5"
              >{@render swatch("var(--muted)")}{m.night_races_waiting({
                count: counts.waiting,
              })}</span
            >
          {/if}
          {#if counts.later > 0}
            <span class="flex items-center gap-1.5"
              >{@render swatch("var(--muted)", HATCH)}{m.night_races_later({
                count: counts.later,
              })}</span
            >
          {/if}
        </span>
      </p>
      <span
        class="flex overflow-hidden rounded-xs bg-muted block-1 inline-full max-inline-80"
        aria-hidden="true"
      >
        <span
          class="bg-foreground transition-[flex-grow] duration-700 motion-reduce:transition-none"
          style:flex-grow={counts.called}
        ></span>
        <span
          class="bg-[color-mix(in_oklab,var(--foreground)_40%,var(--background))] transition-[flex-grow] duration-700 motion-reduce:transition-none"
          style:flex-grow={counts.counting}
        ></span>
        <span style:flex-grow={counts.waiting}></span>
        <span style:flex-grow={counts.later} style:background-image={HATCH}
        ></span>
      </span>
    </div>
    {#if isCompact}
      <button
        type="button"
        aria-expanded={!isFolded}
        aria-controls={id}
        class="inline-flex shrink-0 items-center gap-1 rounded-sm border border-input bg-card ps-3 pe-2 text-[0.8125rem] font-semibold block-9 hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none sm:hidden"
        onclick={() => (openFor = isFolded ? (current ?? "") : null)}
        >{isFolded
          ? m.night_picker_open()
          : m.night_picker_close()}<ChevronDownIcon
          class={cn(
            "transition-transform block-4 inline-4 motion-reduce:transition-none",
            { "rotate-180": !isFolded },
          )}
          aria-hidden="true"
        /></button
      >
    {/if}
    {#if isLong}
      <label
        class={cn(
          "flex items-center gap-2 rounded-sm border border-input bg-card px-2.5 block-8 focus-within:border-foreground focus-within:ring-2 focus-within:ring-ring/40 sm:inline-56 pointer-coarse:block-10 max-sm:inline-full",
          { "max-sm:hidden": isFolded },
        )}
      >
        <SearchIcon
          class="shrink-0 text-muted-foreground block-3.5 inline-3.5"
          aria-hidden="true"
        />
        <input
          bind:value={query}
          type="search"
          enterkeyhint="search"
          autocomplete="off"
          spellcheck="false"
          class="flex-1 bg-transparent text-[0.8125rem] outline-none min-inline-0 [&::-webkit-search-cancel-button]:hidden"
          placeholder={m.night_picker_search()}
          aria-label={m.night_local_search()}
          onkeydown={(event: KeyboardEvent) => {
            if (event.key === "Escape" && query !== "") {
              event.preventDefault();
              event.stopPropagation();
              query = "";
            }
          }}
        />
        {#if query !== ""}
          <button
            type="button"
            class="-me-1 grid place-items-center rounded-xs text-muted-foreground block-6 inline-6 hover:bg-secondary hover:text-foreground"
            aria-label={m.night_local_clear()}
            onclick={() => (query = "")}
          >
            <XIcon class="block-3.5 inline-3.5" aria-hidden="true" />
          </button>
        {/if}
      </label>
    {/if}
  </div>

  <div {id} class={cn("contents", { "max-sm:hidden": isFolded })}>
    {#if groups.length > 1 && !isSearching}
      <div
        class="flex flex-wrap gap-x-5 border-be border-border"
        role="group"
        aria-label={m.night_local_kind()}
      >
        {#each tabs as option (option.key)}
          {const isOn = $derived(option.key === chosen)}
          <button
            type="button"
            aria-pressed={isOn}
            class={cn(
              "-mbe-px flex items-baseline gap-1.5 border-be-2 py-1.5 text-[0.8125rem] font-semibold whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring/50 pointer-coarse:py-2.5",
              {
                "border-foreground text-foreground": isOn,
                "border-transparent text-muted-foreground hover:border-border hover:text-foreground":
                  !isOn,
              },
            )}
            onclick={() => choose(option.key)}
            >{option.label}<span
              class="font-figures text-xs font-medium tabular-nums"
              >{option.count}</span
            ></button
          >
        {/each}
      </div>
    {/if}

    {#each shown as group (group.key)}
      {const entries = $derived(
        group.key === "house" ? group.entries : clip(group.entries),
      )}
      <section class="grid grid-cols-[minmax(0,1fr)] gap-1">
        {#if shown.length > 1 || isSearching || group.prefix !== ""}
          <h3 class="flex items-baseline gap-2 text-xs">
            {#if shown.length > 1 || isSearching}
              <span class="shrink-0 font-semibold">{group.label}</span>
              <span
                class="shrink-0 font-figures text-muted-foreground tabular-nums"
                >{group.entries.length}</span
              >
            {/if}
            {#if group.prefix !== ""}
              <span class="truncate text-muted-foreground">{group.prefix}</span>
            {/if}
          </h3>
        {/if}
        {#if group.key === "house"}
          {@render house(entries)}
        {:else}
          <ul
            class="grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-x-4"
          >
            {#each entries as entry (entry.summary.race.id)}
              {@render row(entry)}
            {/each}
          </ul>
          {#if entries.length < group.entries.length || (isAll && group.entries.length > limit + 1)}
            <button
              type="button"
              class="justify-self-start rounded-xs py-1.5 text-xs font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2.5"
              onclick={() => (isAll = !isAll)}
              >{isAll
                ? m.night_picker_less()
                : m.night_picker_more({ count: group.entries.length })}</button
            >
          {/if}
        {/if}
      </section>
    {:else}
      <p class="py-4 text-sm text-muted-foreground">
        {m.night_index_empty({ query })}
      </p>
    {/each}
  </div>
</nav>
