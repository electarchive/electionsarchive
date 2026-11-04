<script lang="ts">
  import type { LiveEstimate, LiveOption, LiveRace } from "@ea/analytics/live";
  import { resolve } from "$app/paths";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import XIcon from "@lucide/svelte/icons/x";
  import type { UnitStanding } from "#lib/live/standing.js";
  import Face from "#lib/components/live/face.svelte";
  import Also from "#lib/components/live/state/also.svelte";
  import { count, percent } from "#lib/format.js";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { lineup, useReach } from "#lib/live/lineup.js";
  import { liveLinks } from "#lib/live/links.js";
  import { remainder } from "#lib/live/standing.js";
  import { shade, summarize, verdict } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import { ballot, contests, live } from "./locals.js";
  import { BLANK } from "./model.js";

  const {
    name,
    areaId,
    race,
    row,
    estimate,
    pair,
    statewide,
    others,
    slug,
    code,
    stamp,
    href,
    onRace,
    onClose,
  }: {
    name: string;
    areaId: string | null;
    race: LiveRace | null;
    row: UnitStanding | null;
    estimate: LiveEstimate | null;
    pair: [number, number];
    statewide: number | null;
    others: Array<{
      race: LiveRace;
      row: UnitStanding;
      tail: string;
      label: string;
    }>;
    slug: string;
    code: string;
    stamp: string;
    href: string;
    onRace: (tail: string) => void;
    onClose: () => void;
  } = $props();

  const toLive = liveLinks();

  let locals = $state<Awaited<ReturnType<typeof live>> | null>(null);
  let isLoading = $state(true);

  $effect(() => {
    const county = name;
    const at = stamp;
    const path = slug;
    const state = code;
    let isAlive = true;
    isLoading = true;
    void (async () => {
      const found = await ballot(path);
      const picked = found == null ? [] : contests(found, county).slice(0, 30);
      const out =
        found == null || picked.length === 0
          ? []
          : await live(found, picked, state, at);
      if (isAlive) {
        locals = out;
        isLoading = false;
      }
    })();
    return () => {
      isAlive = false;
    };
  });

  const options = $derived(race?.options ?? []);
  const rest = $derived(
    row == null || race == null
      ? null
      : remainder(row, estimate, options, pair),
  );
  const reach = useReach();
  const split = $derived(
    row == null || race == null
      ? null
      : lineup(race, row.votes, reach(), Math.max(5, (race.seats ?? 1) + 1)),
  );
  const rows = $derived(
    split?.main.map((line) => {
      const index = options.indexOf(line.option);
      return {
        option: line.option,
        index,
        votes: line.votes,
        share: line.share ?? 0,
        before: row?.baseline?.shares[index] ?? null,
      };
    }) ?? [],
  );
  const scale = $derived(
    Math.max(
      0.5,
      ...rows.map((entry) => Math.max(entry.share, entry.before ?? 0)),
    ),
  );

  function signedText(value: number | null) {
    if (value == null) {
      return "–";
    }
    const option = options[value >= 0 ? pair[0] : pair[1]];
    return `${surname(option)} +${lead(value)}`;
  }

  const shift = $derived(
    row?.signed == null || row.baseSigned == null
      ? null
      : row.signed - row.baseSigned,
  );
  const toward = $derived<LiveOption | undefined>(
    shift == null ? undefined : options[shift >= 0 ? pair[0] : pair[1]],
  );
  const turnout = $derived(
    row?.baseline == null || row.baseline.turnout <= 0
      ? null
      : row.total / row.baseline.turnout,
  );
  const weight = $derived(
    row?.baseline == null || statewide == null || statewide <= 0
      ? null
      : row.baseline.turnout / statewide,
  );
  const localRows = $derived(
    (locals ?? []).map((entry) => {
      const summary = summarize(entry.race, entry.estimate);
      return {
        ...entry,
        summary,
        color: shade(summary) ?? BLANK,
        line: verdict(summary),
      };
    }),
  );
</script>

<section class="grid gap-4" aria-label={name}>
  <header class="flex items-start justify-between gap-3">
    <div class="grid gap-0.5 min-inline-0">
      <h2 class="text-xl/6 font-[760] tracking-tight text-balance">{name}</h2>
      {#if race != null}
        <a
          href={toLive(race.slug)}
          class="flex items-center gap-1 text-[0.8125rem] text-muted-foreground underline decoration-border underline-offset-3 hover:text-foreground hover:decoration-foreground"
          ><span class="truncate">{race.title}</span><ArrowUpRightIcon
            class="shrink-0 block-3.5 inline-3.5"
            aria-hidden="true"
          /></a
        >
      {/if}
    </div>
    <button
      type="button"
      class="-me-1.5 grid shrink-0 place-items-center rounded-sm text-muted-foreground block-8 inline-8 hover:bg-secondary hover:text-foreground"
      aria-label={m.night_map_close()}
      title={m.night_map_close()}
      onclick={onClose}><XIcon class="block-4 inline-4" /></button
    >
  </header>

  {#if row == null || race == null}
    <p class="text-sm text-muted-foreground">{m.livemap_not_here()}</p>
  {:else}
    <ul class="grid gap-2.5">
      {#each rows as entry (entry.option.id)}
        {const color = $derived(tone(entry.option))}
        <li
          class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2.5"
        >
          {#if entry.option.photo != null || entry.option.person != null}
            <Face option={entry.option} class="block-8 inline-8" />
          {:else}
            <span
              class="mx-2.5 block rounded-full block-3 inline-3"
              style:background-color={color}
            ></span>
          {/if}
          <span class="grid gap-1 min-inline-0">
            <span
              class={cn("truncate", {
                "font-semibold": entry.index === row.leader,
              })}>{entry.option.label}</span
            >
            <span
              class="relative rounded-xs bg-muted block-1.5"
              aria-hidden="true"
            >
              <span
                class="absolute inset-y-0 inset-s-0 rounded-xs transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
                style:inline-size="{(entry.share / scale) * 100}%"
                style:background-color={color}
              ></span>
              {#if entry.before != null && entry.before > 0}
                <span
                  class="absolute -inset-y-0.5 rounded-full bg-foreground/70 inline-0.5"
                  style:inset-inline-start="calc({(entry.before / scale) * 100}%
                  - 1px)"
                ></span>
              {/if}
            </span>
          </span>
          <span class="grid justify-items-end font-figures tabular-nums">
            {#if row.total > 0}
              <span class="font-semibold">{percent(entry.share)}</span>
              <span class="text-xs text-muted-foreground"
                >{count(entry.votes)}</span
              >
            {:else if entry.before != null}
              <span
                class="text-xs text-muted-foreground"
                title={m.night_table_base()}>{percent(entry.before)}</span
              >
            {/if}
          </span>
        </li>
      {/each}
    </ul>
    <Also rest={split?.rest ?? []} total={row.total} />

    <div class="grid gap-1.5">
      <div
        class="relative overflow-hidden rounded-xs bg-muted block-1"
        aria-hidden="true"
      >
        <span
          class="absolute inset-y-0 inset-s-0 bg-foreground transition-[inline-size] duration-700 motion-reduce:transition-none"
          style:inline-size="{Math.min(
            1,
            row.total / Math.max(1, rest?.expected ?? 1),
          ) * 100}%"
        ></span>
      </div>
      <p class="text-xs text-muted-foreground">
        {row.total === 0
          ? m.night_place_waiting()
          : m.night_place_counted({
              share: percent(
                Math.min(1, row.total / Math.max(1, rest?.expected ?? 1)),
                0,
              ),
            })}
      </p>
    </div>

    <div class="grid gap-2 rounded-sm bg-muted/60 p-3 ring-1 ring-border">
      <table class="text-[0.8125rem] inline-full">
        <thead>
          <tr class="text-xs text-muted-foreground">
            <th scope="col" class="text-start font-normal"
              ><span class="sr-only">{m.night_table_county()}</span></th
            >
            <th scope="col" class="text-end font-normal">{m.livemap_now()}</th>
            <th scope="col" class="text-end font-normal"
              >{m.night_table_base()}</th
            >
          </tr>
        </thead>
        <tbody class="font-figures tabular-nums">
          <tr>
            <th scope="row" class="pbs-1 text-start font-normal"
              >{m.night_table_margin()}</th
            >
            <td class="pbs-1 text-end font-semibold"
              >{signedText(row.signed)}</td
            >
            <td class="pbs-1 text-end">{signedText(row.baseSigned)}</td>
          </tr>
          <tr>
            <th scope="row" class="pbs-1 text-start font-normal"
              >{m.night_table_votes()}</th
            >
            <td class="pbs-1 text-end font-semibold"
              >{row.total > 0 ? count(row.total) : "–"}</td
            >
            <td class="pbs-1 text-end"
              >{row.baseline == null ? "–" : count(row.baseline.turnout)}</td
            >
          </tr>
        </tbody>
      </table>
      {#if shift != null && toward != null}
        <p class="flex items-center gap-2 text-[0.8125rem] font-semibold">
          <span
            class="shrink-0 rounded-full block-2.5 inline-2.5"
            style:background-color={tone(toward)}
          ></span>
          {Math.abs(shift) < 0.0005
            ? m.livemap_shift_none()
            : m.livemap_shift({ points: lead(shift), name: surname(toward) })}
        </p>
      {/if}
      {#if turnout != null && row.total > 0}
        <p class="text-xs text-muted-foreground">
          {m.livemap_turnout({ share: percent(turnout, 0) })}
        </p>
      {:else if weight != null}
        <p class="text-xs text-muted-foreground">
          {m.night_place_lede({ share: percent(weight) })}
        </p>
      {/if}
    </div>
  {/if}

  {#if others.length > 0}
    <section class="grid gap-1.5">
      <h3 class="text-xs font-semibold text-muted-foreground">
        {m.night_place_races()}
      </h3>
      <ul class="grid">
        {#each others as other (other.race.id)}
          {const option = $derived(
            other.row.leader == null
              ? null
              : other.race.options[other.row.leader],
          )}
          <li>
            <button
              type="button"
              class="-mx-2 flex items-center gap-2 rounded-sm px-2 text-start text-[0.8125rem] block-9 inline-[calc(100%+1rem)] hover:bg-secondary"
              onclick={() => onRace(other.tail)}
            >
              <span class="grow truncate">{other.label}</span>
              {#if option == null}
                <span class="text-xs text-muted-foreground"
                  >{m.night_tip_waiting()}</span
                >
              {:else}
                <span
                  class="shrink-0 rounded-full block-2 inline-2"
                  style:background-color={tone(option)}
                ></span>
                <span
                  class="shrink-0 font-figures text-xs whitespace-nowrap tabular-nums"
                  >{surname(option)} +{lead(other.row.margin)}</span
                >
              {/if}
            </button>
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  <section class="grid gap-1.5">
    <h3 class="text-xs font-semibold text-muted-foreground">
      {m.night_local_title()}
    </h3>
    {#if isLoading && locals == null}
      <p class="animate-pulse text-xs text-muted-foreground">
        {m.livemap_locals_loading()}
      </p>
    {:else if localRows.length === 0}
      <p class="text-xs text-muted-foreground">{m.livemap_locals_none()}</p>
    {:else}
      <ul class="grid">
        {#each localRows as entry (entry.race.id)}
          <li>
            <a
              href={toLive(entry.race.slug)}
              class="-mx-2 grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 rounded-sm px-2 py-1.5 text-[0.8125rem] hover:bg-secondary"
            >
              <span
                class="rounded-full block-2 inline-2"
                style:background-color={entry.color}
              ></span>
              <span class="truncate"
                >{entry.place === name
                  ? entry.race.title
                  : `${entry.place}: ${entry.race.title}`}</span
              >
              <span></span>
              <span class="truncate text-xs text-muted-foreground"
                >{entry.line}</span
              >
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <nav class="grid gap-1 border-bs pbs-3 text-[0.8125rem] font-semibold">
    <a
      {href}
      class="flex items-center gap-1.5 underline decoration-border underline-offset-3 hover:decoration-foreground"
      >{m.night_map_open({ place: name })}<ArrowUpRightIcon
        class="text-muted-foreground block-3.5 inline-3.5"
        aria-hidden="true"
      /></a
    >
    {#if areaId != null}
      <a
        href={resolve("/id/[id]", { id: areaId })}
        class="flex items-center gap-1.5 underline decoration-border underline-offset-3 hover:decoration-foreground"
        >{m.livemap_archive({ place: name })}<ArrowUpRightIcon
          class="text-muted-foreground block-3.5 inline-3.5"
          aria-hidden="true"
        /></a
      >
    {/if}
  </nav>
</section>
