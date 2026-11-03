<script lang="ts">
  import type { Snippet } from "svelte";
  import { goto } from "$app/navigation";
  import CircleCheckIcon from "@lucide/svelte/icons/circle-check";
  import CircleDashedCheckIcon from "@lucide/svelte/icons/circle-dashed-check";
  import LoaderIcon from "@lucide/svelte/icons/loader-circle";
  import SearchIcon from "@lucide/svelte/icons/search";
  import XIcon from "@lucide/svelte/icons/x";
  import type { Hit } from "#lib/live/find.js";
  import type { Band } from "#lib/live/format.js";
  import type { Summary } from "#lib/live/summary.js";
  import { percent } from "#lib/format.js";
  import { haystack, score, strongest, terms } from "#lib/live/find.js";
  import { lead, stamp, surname, tone } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import { bandOf, verdict } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const {
    path,
    summaries,
    codes,
    labels,
    href,
    link = null,
  }: {
    path: string;
    summaries: Array<Summary>;
    codes: Array<string>;
    labels: Record<string, string>;
    href: (code: string) => string;
    link?: ((slug: string, state: string) => string) | null;
  } = $props();

  const toLive = liveLinks();
  const open = $derived(link ?? ((slug: string) => toLive(slug)));
  const id = $props.id();

  const WORDS = {
    senate: ["senate", "senator", "sen"],
    governor: ["governor", "gov", "gubernatorial"],
    house: ["house", "representative", "rep", "congress", "district", "cd"],
    other: [],
  };
  const ORDER = { senate: 0, governor: 1, house: 2, other: 3 };
  const OFFICES: Partial<Record<Summary["race"]["office"], () => string>> = {
    senate: m.night_nation_senate,
    governor: m.night_nation_governor,
    house: m.night_nation_house,
  };

  let query = $state("");
  let isOpen = $state(false);
  let active = $state(0);
  let input = $state<HTMLInputElement>();
  let remote = $state<{ q: string; hits: Array<Hit> } | null>(null);
  let pending = $state<string | null>(null);
  const answers = new Map<string, Array<Hit>>();

  function name(code: string) {
    return labels[code] ?? code.toUpperCase();
  }

  function number(summary: Summary) {
    const found = Number.parseInt(summary.race.district ?? "", 10);
    return Number.isNaN(found) ? 0 : found;
  }

  function title(summary: Summary) {
    const { race } = summary;
    const state = name(race.state);
    if (race.office === "senate") {
      return race.slug.endsWith("-special")
        ? m.night_search_senate_special({ state })
        : m.night_search_senate({ state });
    }
    if (race.office === "governor") {
      return m.night_search_governor({ state });
    }
    if (race.office === "house") {
      const seat = number(summary);
      return seat === 0
        ? m.night_search_at_large({ state })
        : m.night_search_house({ state, district: String(seat) });
    }
    return race.title;
  }

  function code(summary: Summary) {
    const { race } = summary;
    if (race.office !== "house") {
      return race.state.toUpperCase();
    }
    const seat = number(summary);
    return `${race.state.toUpperCase()}-${seat === 0 ? "AL" : seat}`;
  }

  const races = $derived(
    summaries.map((summary) => ({
      summary,
      hay: haystack([
        title(summary),
        name(summary.race.state),
        summary.race.state,
        ...WORDS[summary.race.office],
        ...(summary.race.office === "house" && number(summary) === 0
          ? ["at large", "al"]
          : []),
        ...summary.race.options.flatMap((option) =>
          option.party == null ? [] : [option.party.short, option.party.name],
        ),
      ]),
      people: summary.race.options.flatMap((option) =>
        option.isWriteIn
          ? []
          : [{ name: option.label, hay: haystack([option.label]) }],
      ),
    })),
  );
  const places = $derived(
    codes.map((entry) => ({
      code: entry,
      hay: haystack([name(entry), entry]),
      lead: summaries
        .filter((summary) => summary.race.state === entry)
        .toSorted((a, b) => ORDER[a.race.office] - ORDER[b.race.office])[0],
    })),
  );

  const wanted = $derived(terms(query));
  const key = $derived(wanted.join(" "));

  const found = $derived.by(() => {
    if (wanted.length === 0) {
      return {
        states: [],
        races: summaries
          .filter(
            (summary) =>
              summary.total > 0 &&
              summary.winner == null &&
              summary.projected == null &&
              summary.race.office !== "other",
          )
          .toSorted((a, b) => a.margin - b.margin)
          .slice(0, 6)
          .map((summary) => ({ summary, match: null })),
      };
    }
    const states = places
      .flatMap((entry) => {
        const hit = score(entry.hay, [], wanted);
        return hit == null ? [] : [{ ...entry, total: hit.total }];
      })
      .toSorted((a, b) => b.total - a.total);
    const kept = strongest(states).slice(0, 3);
    const hits = strongest(
      races
        .flatMap((entry) => {
          const hit = score(entry.hay, entry.people, wanted);
          return hit == null ? [] : [{ summary: entry.summary, ...hit }];
        })
        .toSorted(
          (a, b) =>
            b.total - a.total ||
            ORDER[a.summary.race.office] - ORDER[b.summary.race.office] ||
            a.summary.race.state.localeCompare(b.summary.race.state) ||
            number(a.summary) - number(b.summary),
        ),
    ).slice(0, kept.length > 0 && wanted.length === 1 ? 5 : 8);
    return { states: kept, races: hits };
  });

  const locals = $derived(
    remote != null && remote.q === key && wanted.length > 0 ? remote.hits : [],
  );
  const isSearching = $derived(
    pending === key && wanted.length > 0 && locals.length === 0,
  );

  const rows = $derived([
    ...found.states.map((entry) => ({
      key: `state:${entry.code}`,
      href: href(entry.code),
    })),
    ...found.races.map((entry) => ({
      key: `race:${entry.summary.race.id}`,
      href: open(entry.summary.race.slug, entry.summary.race.state),
    })),
    ...locals.map((entry) => ({
      key: `local:${entry.slug}`,
      href: open(entry.slug, entry.state),
    })),
  ]);
  const isShown = $derived(
    isOpen && (rows.length > 0 || wanted.length > 0 || isSearching),
  );

  $effect(() => {
    const q = key;
    if (q.length < 2) {
      return;
    }
    const held = answers.get(q);
    if (held != null) {
      remote = { q, hits: held };
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      pending = q;
      orpc.live.find
        .call({ path, q }, { signal: controller.signal })
        .then((hits) => {
          answers.set(q, hits);
          remote = { q, hits };
        })
        .catch(() => {})
        .finally(() => {
          if (pending === q) {
            pending = null;
          }
        });
    }, 140);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  });

  let isWarm = false;
  function warm() {
    isOpen = true;
    if (!isWarm) {
      isWarm = true;
      orpc.live.find.call({ path, q: "" }).catch(() => {
        isWarm = false;
      });
    }
  }

  function onshortcut(event: KeyboardEvent) {
    if (
      event.key !== "/" ||
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      event.defaultPrevented
    ) {
      return;
    }
    const target = event.target;
    if (
      target instanceof HTMLElement &&
      (target.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
    ) {
      return;
    }
    event.preventDefault();
    input?.focus();
    input?.select();
  }

  function show(index: number) {
    document
      .getElementById(`${id}-${index}`)
      ?.scrollIntoView({ block: "nearest" });
  }

  function onkeydown(event: KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      isOpen = true;
      if (rows.length > 0) {
        active =
          (active + (event.key === "ArrowDown" ? 1 : -1) + rows.length) %
          rows.length;
        show(active);
      }
    } else if (event.key === "Enter") {
      const row = rows[active];
      if (row != null && isShown) {
        event.preventDefault();
        if (event.metaKey || event.ctrlKey) {
          window.open(row.href, "_blank", "noopener");
        } else {
          isOpen = false;
          void goto(row.href);
        }
      }
    } else if (event.key === "Escape") {
      event.preventDefault();
      if (query === "") {
        isOpen = false;
        input?.blur();
      } else {
        query = "";
      }
    }
  }

  function status(summary: Summary, band: Band | null) {
    if (band != null) {
      return {
        option: summary.projected ?? summary.winner,
        text: verdict(summary),
      };
    }
    if (summary.leader != null) {
      return {
        option: summary.leader,
        text: `${surname(summary.leader)} +${lead(summary.margin)}`,
      };
    }
    return {
      option: undefined,
      text: summary.later == null ? m.night_nation_waiting() : m.night_later(),
    };
  }
</script>

<svelte:window onkeydown={onshortcut} />

{#snippet heading(text: string)}
  <li
    role="presentation"
    class="px-3 pbs-2.5 pbe-1 text-xs font-semibold text-muted-foreground"
  >
    {text}
  </li>
{/snippet}

{#snippet option(index: number, band: Band | null, body: Snippet)}
  {const isActive = $derived(active === index)}
  <li role="presentation" class="border-be border-border last:border-be-0">
    <a
      id={`${id}-${index}`}
      href={rows[index]?.href}
      role="option"
      tabindex="-1"
      aria-selected={isActive}
      data-sveltekit-preload-data="hover"
      class={cn(
        "grid grid-cols-[3rem_minmax(0,1fr)] items-center gap-x-3 gap-y-0.5 border-s-2 py-2 ps-2.5 pe-3 text-sm outline-none sm:grid-cols-[3rem_minmax(0,1fr)_auto]",
        { "border-foreground": isActive, "border-transparent": !isActive },
        { "bg-accent": isActive && band == null, band: band != null },
      )}
      style:--party={band == null
        ? undefined
        : isActive
          ? `color-mix(in oklab, ${band.fill} 84%, black)`
          : band.fill}
      onpointermove={() => (active = index)}
      onclick={() => (isOpen = false)}
    >
      {@render body()}
    </a>
  </li>
{/snippet}

{#snippet tag(text: string)}
  <span
    class="font-figures text-xs font-semibold tracking-wide whitespace-nowrap text-muted-foreground tabular-nums"
    >{text}</span
  >
{/snippet}

{#snippet standing(summary: Summary, band: Band | null, isWide: boolean)}
  {const now = $derived(status(summary, band))}
  {#if band?.isProjected === true}
    <CircleDashedCheckIcon
      class="shrink-0 block-3.5 inline-3.5"
      aria-label={m.night_band_projected()}
    >
      <title>{m.night_band_called({ time: stamp(summary.projectedAt) })}</title>
    </CircleDashedCheckIcon>
  {:else if band != null}
    <CircleCheckIcon
      class="shrink-0 block-3.5 inline-3.5"
      aria-label={m.night_state_won()}
    />
  {:else}
    <span
      class={cn("shrink-0 rounded-full block-2 inline-2", {
        "ring-1 ring-muted-foreground/50 ring-inset": now.option == null,
      })}
      style:background-color={now.option == null ? undefined : tone(now.option)}
      aria-hidden="true"
    ></span>
  {/if}
  <span
    class={cn("truncate font-figures tabular-nums", {
      "font-semibold": band != null,
      "text-muted-foreground": now.option == null,
    })}>{now.text}</span
  >
  {#if isWide && band == null && summary.counted != null && summary.later == null}
    <span
      class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums max-sm:hidden"
      >{percent(summary.counted, 0)}</span
    >
  {/if}
{/snippet}

{#snippet race(summary: Summary, match: string | null, index: number)}
  {const band = $derived(bandOf(summary))}
  {#snippet body()}
    {@render tag(code(summary))}
    <span class="grid min-inline-0">
      <span class="truncate font-semibold">{title(summary)}</span>
      {#if match != null}
        <span class="truncate text-xs text-muted-foreground">{match}</span>
      {/if}
    </span>
    <span
      class="col-start-2 flex items-center gap-1.5 min-inline-0 sm:col-start-3 sm:row-start-1 sm:max-inline-60"
    >
      {@render standing(summary, band, true)}
    </span>
  {/snippet}
  {@render option(index, band, body)}
{/snippet}

<div
  class="relative max-inline-xl"
  onfocusout={(event) => {
    const next = event.relatedTarget;
    if (!(next instanceof Node) || !event.currentTarget.contains(next)) {
      isOpen = false;
    }
  }}
>
  <label for={`${id}-input`} class="sr-only">{m.night_search_label()}</label>
  <div
    class="flex field items-center gap-2 ps-3 pe-2 block-10 focus-within:border-foreground focus-within:shadow-[inset_0_-3px_0_var(--primary)]"
  >
    <SearchIcon
      class="shrink-0 text-muted-foreground block-4 inline-4"
      aria-hidden="true"
    />
    <input
      bind:this={input}
      bind:value={query}
      id={`${id}-input`}
      type="text"
      role="combobox"
      autocomplete="off"
      autocapitalize="off"
      spellcheck="false"
      enterkeyhint="go"
      aria-keyshortcuts="/"
      aria-autocomplete="list"
      aria-expanded={isShown}
      aria-controls={`${id}-list`}
      aria-activedescendant={isShown && rows[active] != null
        ? `${id}-${active}`
        : undefined}
      placeholder={m.night_search_placeholder()}
      class="flex-1 bg-transparent text-base outline-none min-inline-0 placeholder:text-muted-foreground sm:text-sm"
      onfocus={warm}
      oninput={() => {
        isOpen = true;
        active = 0;
      }}
      {onkeydown}
    />
    {#if isSearching}
      <LoaderIcon
        class="shrink-0 animate-spin text-muted-foreground block-4 inline-4 motion-reduce:animate-none"
        aria-hidden="true"
      />
    {/if}
    {#if query !== ""}
      <button
        type="button"
        class="grid shrink-0 place-items-center rounded-sm text-muted-foreground block-6 inline-6 hover:bg-secondary hover:text-foreground"
        aria-label={m.night_search_clear()}
        onclick={() => {
          query = "";
          input?.focus();
        }}
      >
        <XIcon class="block-3.5 inline-3.5" aria-hidden="true" />
      </button>
    {:else}
      <kbd
        class="grid shrink-0 place-items-center rounded-sm border border-border font-figures text-xs text-muted-foreground block-5 inline-5 max-sm:hidden"
        aria-hidden="true">/</kbd
      >
    {/if}
  </div>
  {#if isShown}
    <ul
      id={`${id}-list`}
      role="listbox"
      aria-label={m.night_search_label()}
      class="absolute inset-x-0 inset-bs-full z-40 mbs-1 overflow-y-auto overscroll-contain rounded-sm bg-popover pbe-1 text-foreground shadow-menu ring-1 ring-border max-block-[min(32rem,70dvh)]"
    >
      {#if wanted.length === 0}
        {@render heading(m.night_search_closest())}
      {/if}
      {#if found.states.length > 0}
        {@render heading(m.night_search_states())}
        {#each found.states as entry, index (entry.code)}
          {#snippet body()}
            {@render tag(entry.code.toUpperCase())}
            <span class="grid min-inline-0">
              <span class="truncate font-semibold">{name(entry.code)}</span>
              <span class="truncate text-xs text-muted-foreground"
                >{m.night_search_state_page()}</span
              >
            </span>
            {#if entry.lead != null}
              <span
                class="col-start-2 flex items-center gap-1.5 min-inline-0 sm:col-start-3 sm:row-start-1 sm:max-inline-60"
              >
                <span
                  class="shrink-0 text-xs text-muted-foreground max-sm:hidden"
                  >{OFFICES[entry.lead.race.office]?.()}</span
                >
                {@render standing(entry.lead, bandOf(entry.lead), false)}
              </span>
            {/if}
          {/snippet}
          {@render option(index, null, body)}
        {/each}
      {/if}
      {#if found.races.length > 0}
        {#if wanted.length > 0}
          {@render heading(m.night_search_races())}
        {/if}
        {#each found.races as entry, index (entry.summary.race.id)}
          {@render race(
            entry.summary,
            entry.match,
            found.states.length + index,
          )}
        {/each}
      {/if}
      {#if locals.length > 0 || isSearching}
        {@render heading(m.night_search_local())}
        {#if isSearching}
          <li
            role="presentation"
            class="px-3 py-2 text-sm text-muted-foreground"
          >
            {m.night_search_loading()}
          </li>
        {/if}
        {#each locals as entry, index (entry.slug)}
          {#snippet body()}
            {@render tag((entry.slug.split("/", 2)[1] ?? "").toUpperCase())}
            <span class="grid min-inline-0">
              <span class="truncate font-semibold">{entry.title}</span>
              <span class="truncate text-xs text-muted-foreground"
                >{entry.place}{#if entry.match != null}<span
                    class="text-foreground">, {entry.match}</span
                  >{/if}</span
              >
            </span>
            {#if entry.isMeasure}
              <span
                class="text-xs whitespace-nowrap text-muted-foreground max-sm:hidden"
                >{m.night_search_measure()}</span
              >
            {/if}
          {/snippet}
          {@render option(
            found.states.length + found.races.length + index,
            null,
            body,
          )}
        {/each}
      {/if}
      {#if rows.length === 0 && !isSearching && wanted.length > 0 && (key.length < 2 || remote?.q === key)}
        <li role="presentation" class="p-3 text-sm text-muted-foreground">
          {m.night_search_none({ query: query.trim() })}
        </li>
      {/if}
    </ul>
  {/if}
  <p class="sr-only" aria-live="polite">
    {isShown && wanted.length > 0
      ? m.night_search_count({ count: rows.length })
      : ""}
  </p>
</div>
