<script lang="ts">
  import { browser } from "$app/env";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import { queryOptions, useQueryClient } from "@tanstack/svelte-query";
  import { onDestroy, untrack } from "svelte";
  import { learn, Pending, load as unpack } from "svelte-islands";
  import Export from "#lib/components/export.svelte";
  import Name from "#lib/components/name.svelte";
  import { count, date } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Balance from "./balance.svelte";
  import PlaceMap from "./map.svelte";
  import Seats from "./seats.svelte";

  const {
    id,
    place,
    latest,
    seed,
    recordSeed,
    isExpected,
  }: {
    id: string;
    place: string;
    isExpected: boolean;
    latest: { path: string | null; date: string | null } | null;
    seed: Extract<PageData, { kind: "polity" }>["polity"]["scrubber"];
    recordSeed: Extract<PageData, { kind: "polity" }>["polity"]["record"];
  } = $props();

  const uid = $props.id();
  const client = useQueryClient();
  const wanted = untrack(
    () => Number(page.url.searchParams.get("year")) || null,
  );
  const recordOptions = untrack(() =>
    orpc.polities.record.queryOptions({ input: { id } }),
  );

  async function load(path: string) {
    const held =
      path === latest?.path
        ? await Promise.resolve(seed).catch(() => null)
        : null;
    const seeded = held == null ? null : await unpack(held).catch(() => null);
    const found = seeded?.path === path ? seeded : null;
    const located =
      found == null
        ? await client.fetchQuery(
            orpc.elections.locate.queryOptions({ input: { path } }),
          )
        : found.located;
    const input = { input: { id: located.id } };
    if (found != null) {
      client.setQueryData(orpc.elections.get.queryKey(input), found.hub);
      client.setQueryData(orpc.seats.hexes.queryKey(input), found.hexes);
    }
    const [hub, hexes] = await Promise.all([
      client.fetchQuery(orpc.elections.get.queryOptions(input)),
      client.fetchQuery(orpc.seats.hexes.queryOptions(input)).catch(() => null),
    ]);
    const race =
      hub.races.find((entry) => entry.map != null && entry.places != null) ??
      null;
    const states =
      race == null
        ? await client.fetchQuery(orpc.elections.states.queryOptions(input))
        : null;
    return { hub, hexes, race, states };
  }
  async function loadRecord() {
    const held = await Promise.resolve(recordSeed).catch(() => null);
    const seeded = held == null ? null : await unpack(held).catch(() => null);
    return seeded ?? orpc.polities.record.call({ id });
  }
  const viewOptions = (path: string) =>
    queryOptions({
      queryKey: ["scrubber", path] as const,
      queryFn: async () => load(path),
    });

  const stored = client.getQueryData(recordOptions.queryKey) ?? null;
  let record = $state(stored);
  let isRecordSettled = $state(stored != null);
  let current = $state<{
    path: string;
    view: Awaited<ReturnType<typeof load>> | null;
    isFailed: boolean;
  } | null>(null);
  let previous = $state<Awaited<ReturnType<typeof load>> | null>(null);
  let at = $state<number | null>(null);
  let isPlaying = $state(false);
  let isDragging = false;
  let isEngaged = false;
  let swipe: { x: number; y: number } | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let playback: ReturnType<typeof setInterval> | undefined;

  const parties = $derived(
    new Map((record?.parties ?? []).map((party) => [party.id, party])),
  );
  const tint = (party: string | null) =>
    party == null
      ? swatch(null, "")
      : swatch(parties.get(party)?.color ?? null, `party:${party}`);
  const paint = (item: {
    color: string | null;
    key: string;
    party: string | null;
  }) => swatch(parties.get(item.party ?? "")?.color ?? item.color, item.key);

  const own = $derived(
    (record?.series ?? []).filter((series) => !series.isAbroad),
  );
  const chamber = $derived(
    own
      .filter((series) => !series.isOffice)
      .toSorted((a, b) => b.points.length - a.points.length)[0] ?? null,
  );
  const office = $derived(
    own
      .filter((series) => series.isOffice)
      .toSorted((a, b) => b.points.length - a.points.length)[0] ?? null,
  );
  const steps = $derived(
    Map.groupBy(
      own.flatMap((series) =>
        series.points.flatMap((point) =>
          point.path == null
            ? []
            : [{ path: point.path, year: point.year, date: point.date }],
        ),
      ),
      (point) => point.path,
    )
      .values()
      .map((group) => group[0])
      .filter((point) => point != null)
      .toArray()
      .toSorted((a, b) => a.date.localeCompare(b.date))
      .map((point) => {
        const seated = chamber?.points.find(
          (entry) => entry.path === point.path,
        );
        const held = office?.points.find((entry) => entry.path === point.path);
        const top = (seated ?? held)?.parties.toSorted(
          (a, b) => b.seats - a.seats,
        )[0];
        return { ...point, tone: tint(top?.party ?? null) };
      }),
  );
  const asked = $derived(
    wanted == null ? -1 : steps.findIndex((entry) => entry.year >= wanted),
  );
  const index = $derived(
    Math.min(at ?? (asked === -1 ? steps.length - 1 : asked), steps.length - 1),
  );

  const step = $derived(
    steps[index] ??
      (latest?.path == null ||
      latest.date == null ||
      (!isRecordSettled && (wanted != null || isExpected))
        ? null
        : {
            path: latest.path,
            date: latest.date,
            year: Number(latest.date.slice(0, 4)),
            tone: "",
          }),
  );
  const isSeries = $derived(
    steps.length > 1 || (isExpected && !isRecordSettled),
  );
  const first = $derived(steps[0]?.year ?? 0);
  const last = $derived(steps.at(-1)?.year ?? 0);
  const along = (year: number) =>
    last === first ? 0.5 : (year - first) / (last - first);

  const nearby = () => {
    if (!isEngaged || isDragging) {
      return;
    }
    for (const near of [steps[index - 1], steps[index + 1]]) {
      if (near != null) {
        void client.prefetchQuery(viewOptions(near.path));
      }
    }
  };
  const show = (path: string) => {
    if (current?.path === path && !current.isFailed) {
      return;
    }
    if (current?.view != null) {
      previous = current.view;
    }
    const cached = client.getQueryData(viewOptions(path).queryKey);
    current = { path, view: cached ?? null, isFailed: false };
    if (cached != null) {
      nearby();
      return;
    }
    client.fetchQuery(viewOptions(path)).then(
      (view) => {
        if (current?.path === path) {
          current = { path, view, isFailed: false };
          nearby();
        }
      },
      () => {
        if (current?.path === path) {
          current = { path, view: null, isFailed: true };
        }
      },
    );
  };
  const sync = () => {
    clearTimeout(timer);
    if (step != null) {
      show(step.path);
    }
  };
  const go = (next: number, delay = 0) => {
    at = next;
    keep({
      year:
        index === steps.length - 1 ? null : String(steps[index]?.year ?? ""),
    });
    if (delay === 0) {
      sync();
    } else {
      clearTimeout(timer);
      timer = setTimeout(sync, delay);
    }
  };
  const stop = () => {
    isPlaying = false;
    clearInterval(playback);
  };
  const play = () => {
    if (index >= steps.length - 1) {
      go(0);
    }
    isPlaying = true;
    clearInterval(playback);
    playback = setInterval(() => {
      if (index >= steps.length - 1) {
        stop();
      } else {
        go(index + 1);
      }
    }, 1600);
  };
  const engage = () => {
    if (!isEngaged) {
      isEngaged = true;
      nearby();
    }
  };

  if (browser) {
    untrack(() => {
      sync();
      if (!isRecordSettled) {
        client
          .fetchQuery(queryOptions({ ...recordOptions, queryFn: loadRecord }))
          .then(
            (data) => {
              record = data;
            },
            () => {},
          )
          .finally(() => {
            isRecordSettled = true;
            sync();
          });
      }
    });
  }
  onDestroy(() => {
    clearTimeout(timer);
    clearInterval(playback);
  });

  const view = $derived(
    current?.isFailed === true ? null : (current?.view ?? previous),
  );
  const hub = $derived(view?.hub ?? null);
  const race = $derived(view?.race ?? null);
  const places = $derived.by(() => {
    const own = race?.places;
    if (race == null || own == null || race.count <= 1) {
      return own ?? null;
    }
    const later = (hub?.races ?? [])
      .filter(
        (other) =>
          other.name === race.name &&
          (other.round ?? 1) > (race.round ?? 1) &&
          other.places?.level === own.level,
      )
      .toSorted((a, b) => (a.round ?? 1) - (b.round ?? 1));
    const finals = new Map(
      later.flatMap((other) =>
        (other.places?.rows ?? []).map((row) => [
          row.k,
          { row, legend: other.places?.legend ?? [] },
        ]),
      ),
    );
    if (finals.size === 0) {
      return own;
    }
    const legend = [
      ...own.legend,
      ...Map.groupBy(
        later.flatMap((other) => other.places?.legend ?? []),
        (item) => item.key,
      )
        .values()
        .flatMap((items) =>
          items[0] == null ||
          own.legend.some((item) => item.key === items[0]?.key)
            ? []
            : [items[0]],
        ),
    ];
    const at = new Map(legend.map((item, spot) => [item.key, spot]));
    return {
      ...own,
      legend,
      districts: undefined,
      rows: own.rows.map((row) => {
        const final = finals.get(row.k);
        if (final == null) {
          return {
            ...row,
            shares: legend.map((_, spot) => row.shares[spot] ?? 0),
          };
        }
        const shares = legend.map(() => 0);
        for (const [spot, item] of final.legend.entries()) {
          shares[at.get(item.key) ?? 0] = final.row.shares[spot] ?? 0;
        }
        return {
          ...row,
          turnout: final.row.turnout,
          valid: final.row.valid,
          shares,
          leader:
            final.row.leader == null
              ? null
              : (at.get(final.legend[final.row.leader]?.key ?? "") ?? null),
          margin: final.row.margin,
          earlier: null,
          prior: null,
        };
      }),
    };
  });
  const isStale = $derived(
    current?.view == null || current.path !== step?.path,
  );
  const balance = $derived.by(() => {
    if (race == null) {
      return null;
    }
    const lean = (row: { lean: number | null }) => row.lean ?? 1e9;
    if (race.electors != null && race.electors.rows.length > 0) {
      let total = race.electors.appointed ?? 0;
      if (race.electors.appointed == null) {
        for (const row of race.electors.rows) {
          total += row.votes;
        }
      }
      return {
        parts: race.electors.rows
          .toSorted((a, b) => lean(a) - lean(b) || b.votes - a.votes)
          .map((row) => ({
            key: row.key,
            label: row.person == null ? (row.short ?? row.label) : row.label,
            color: paint(row),
            value: row.votes,
            person: row.person,
            party: row.party,
          })),
        total,
        isCount: true,
        isWhole: true,
      };
    }
    const isSeated = race.results.some((row) => (row.seats ?? 0) > 1);
    let seats = 0;
    for (const row of race.results) {
      seats += row.seats ?? 0;
    }
    return {
      parts: race.results
        .toSorted((a, b) => lean(a) - lean(b) || b.share - a.share)
        .map((row) => ({
          key: row.key,
          label: row.person == null ? (row.short ?? row.label) : row.label,
          color: paint(row),
          value: isSeated ? (row.seats ?? 0) : row.share,
          person: row.person,
          party: row.party,
        })),
      total: isSeated ? seats : 1,
      isCount: isSeated,
      isWhole: false,
    };
  });
  const standing = $derived(
    chamber?.points.findLast((entry) => entry.date <= (step?.date ?? "")) ??
      null,
  );
  const sitting = $derived(
    standing?.parties
      .filter((row) => row.seats > 0)
      .map((row) => {
        const party = parties.get(row.party ?? "");
        return {
          key:
            row.party == null
              ? row.isIndependent
                ? "independent"
                : "none"
              : `party:${row.party}`,
          label:
            row.label ??
            party?.label ??
            (row.isIndependent ? m.seats_independents() : m.record_none()),
          short:
            row.label == null ? (party?.short ?? null) : (row.short ?? null),
          color: party?.color ?? null,
          lean: party?.lean ?? null,
          seats: row.seats,
          here: 0,
          members: [],
        };
      }) ?? [],
  );
  const government = $derived.by(() => {
    const from = step?.date ?? "";
    const next =
      steps[index + 1]?.date ??
      `${Number(from.slice(0, 4)) + 2}${from.slice(4)}`;
    const all = record?.governments ?? [];
    return (
      all.find((entry) => entry.from > from && entry.from < next) ??
      all.findLast(
        (entry) => entry.from <= from && (entry.to == null || entry.to > from),
      ) ??
      null
    );
  });

  const pick = (event: PointerEvent) => {
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const x = (event.clientX - box.left) / Math.max(box.width, 1);
    let best = { index: 0, distance: Infinity };
    for (const [spot, entry] of steps.entries()) {
      const distance = Math.abs(along(entry.year) - x);
      if (distance < best.distance) {
        best = { index: spot, distance };
      }
    }
    if (best.index !== index) {
      go(best.index, 260);
    }
  };
</script>

{#if !isRecordSettled || step != null}
  <section
    class="relative col-span-12 grid gap-4 bg-card px-3.5 pbs-4 pbe-4"
    aria-labelledby="{uid}-title"
    data-export
    onpointerenter={engage}
    onfocusin={engage}
  >
    <Export name="history" class="absolute inset-e-2 inset-bs-2" />
    <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 pe-8">
      <div class="grid gap-0.5">
        <h2
          id="{uid}-title"
          class="text-[clamp(1.375rem,2.2vw,1.75rem)]/tight font-[760] tracking-[-0.015em]"
        >
          {isSeries ? m.scrubber_title({ place }) : m.polity_latest()}
        </h2>
        {#if isSeries}
          <p
            class="text-[0.875rem] text-pretty text-muted-foreground max-inline-[64ch]"
          >
            {m.scrubber_lede()}
          </p>
        {/if}
      </div>
      {#if isSeries}
        <button
          type="button"
          class="flex items-center gap-1.5 rounded-sm border border-input bg-card ps-2 pe-3 text-[0.8125rem] font-semibold block-8 hover:bg-secondary"
          aria-pressed={isPlaying}
          disabled={steps.length <= 1}
          onclick={() => {
            if (isPlaying) {
              stop();
            } else {
              play();
            }
          }}
        >
          {#if isPlaying}
            <PauseIcon class="block-4 inline-4" />{m.scrubber_pause()}
          {:else}
            <PlayIcon class="block-4 inline-4" />{m.scrubber_play()}
          {/if}
        </button>
      {/if}
    </div>

    {#if isSeries}
      <Pending
        name="scrubber.slider"
        isPending={steps.length <= 1}
        reserve="4.8125rem"
        class="[--island-inset:0]"
      >
        <div class="grid gap-1 pbs-5">
          <div
            class="relative cursor-grab touch-pan-y rounded-sm outline-offset-4 select-none block-9 before:absolute before:inset-x-0 before:-inset-y-2.5 active:cursor-grabbing"
            role="slider"
            tabindex="0"
            aria-label={m.scrubber_slider()}
            aria-valuemin={first}
            aria-valuemax={last}
            aria-valuenow={step?.year ?? last}
            aria-valuetext={step == null ? undefined : date(step.date)}
            onpointerdown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              isDragging = true;
              stop();
              pick(event);
            }}
            onpointermove={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                pick(event);
              }
            }}
            onpointerup={() => {
              isDragging = false;
              sync();
            }}
            onpointercancel={() => {
              isDragging = false;
              sync();
            }}
            onkeydown={(event) => {
              const moves: Record<string, number> = {
                ArrowLeft: -1,
                ArrowDown: -1,
                ArrowRight: 1,
                ArrowUp: 1,
                PageDown: -5,
                PageUp: 5,
                Home: -steps.length,
                End: steps.length,
              };
              const move = moves[event.key];
              if (move != null) {
                event.preventDefault();
                stop();
                go(Math.max(0, Math.min(steps.length - 1, index + move)));
              }
            }}
          >
            <span
              class="absolute inset-x-0 inset-bs-1/2 bg-border block-px"
              aria-hidden="true"
            ></span>
            {#each steps as entry, spot (entry.path)}
              <span
                class={cn(
                  "absolute -translate-x-1/2 rounded-full transition-[block-size,opacity] duration-200 inline-1 motion-reduce:transition-none",
                  {
                    "inset-bs-3 opacity-45 block-3": spot !== index,
                    "inset-bs-0 block-9": spot === index,
                  },
                )}
                style:inset-inline-start="{along(entry.year) * 100}%"
                style:background-color={entry.tone}
                aria-hidden="true"
              ></span>
            {/each}
            {#if step != null}
              <span
                class="pointer-events-none absolute inset-be-[calc(100%+0.375rem)] rounded-xs bg-foreground px-1.5 font-figures text-xs font-semibold text-background tabular-nums transition-[inset-inline-start] duration-200 motion-reduce:transition-none"
                style:inset-inline-start="{along(step.year) * 100}%"
                style:translate="{-along(step.year) * 100}% 0"
                aria-hidden="true">{step.year}</span
              >
            {/if}
          </div>
          <div
            class="flex justify-between font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
            aria-hidden="true"
          >
            <span>{first}</span><span>{last}</span>
          </div>
        </div>
      </Pending>
    {/if}

    <div
      class="grid gap-x-6 gap-y-4 lg:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)]"
    >
      <div
        class={cn(
          "transition-opacity duration-300 min-inline-0 motion-reduce:transition-none",
          { "opacity-60": isStale },
        )}
        {@attach current?.view == null ? undefined : learn("scrubber.map")}
      >
        {#if step != null || isSeries}
          <div
            class="mbe-2 flex touch-pan-y items-center gap-2 min-block-11 lg:hidden"
            role="group"
            aria-label={m.scrubber_slider()}
            onpointerdown={(event) => {
              if (event.pointerType === "touch") {
                swipe = { x: event.clientX, y: event.clientY };
              }
            }}
            onpointerup={(event) => {
              if (swipe != null) {
                const dx = event.clientX - swipe.x;
                if (
                  Math.abs(dx) > 40 &&
                  Math.abs(dx) > Math.abs(event.clientY - swipe.y) * 1.5
                ) {
                  stop();
                  go(
                    Math.max(
                      0,
                      Math.min(steps.length - 1, index + (dx < 0 ? 1 : -1)),
                    ),
                  );
                }
              }
              swipe = null;
            }}
            onpointercancel={() => (swipe = null)}
          >
            <p
              class="flex items-baseline gap-2 min-inline-0"
              aria-live="polite"
            >
              {#if step != null}
                <span
                  class="font-figures text-4xl/none font-[780] tracking-[-0.02em]"
                  >{step.year}</span
                >
              {/if}
              {#if hub != null && step != null}
                <a
                  class="truncate text-[0.8125rem] font-semibold underline decoration-border underline-offset-4"
                  href={resolve("/[...path]", { path: step.path })}
                  >{hub.name}</a
                >
              {/if}
            </p>
            {#if steps.length > 1}
              <div class="ms-auto flex shrink-0 gap-1">
                {#each [{ move: -1, label: m.scrubber_previous(), icon: ChevronLeftIcon }, { move: 1, label: m.scrubber_next(), icon: ChevronRightIcon }] as button (button.move)}
                  <button
                    type="button"
                    class="grid place-items-center rounded-sm border border-input bg-card block-11 inline-11 hover:bg-secondary disabled:opacity-40"
                    aria-label={button.label}
                    disabled={index + button.move < 0 ||
                      index + button.move > steps.length - 1}
                    onclick={() => {
                      stop();
                      go(index + button.move);
                    }}
                  >
                    <button.icon class="block-5 inline-5" />
                  </button>
                {/each}
              </div>
            {/if}
          </div>
          {#if hub?.coverage != null}
            <p class="mbe-2 text-[0.8125rem] font-semibold lg:hidden">
              {m.election_partial({
                held: count(hub.coverage.held),
                of: count(hub.coverage.of),
                noun: hub.coverage.noun,
              })}
            </p>
          {/if}
        {/if}
        {#if race?.map != null && places != null}
          {#if race.map.tiles != null}
            {#key `${race.map.tiles}:${race.map.own?.tiles ?? ""}`}
              <PlaceMap
                {places}
                isFinal
                colors={places.legend.map(paint)}
                tiles={race.map.tiles}
                hexes={view?.hexes ?? null}
                own={race.map.own}
                nested={race.map.nested}
                frame={race.map.frame}
                blank={race.map.blank ?? null}
                outlying={race.map.outlying ?? null}
                parted={race.map.parted ?? null}
                approximate={race.map.approximate}
                sources={race.map.credits}
                noun={places.levels[0]?.noun ?? ""}
                votes={m.col_votes()}
                names={places.legend.map((item) =>
                  item.person == null ? (item.short ?? item.label) : item.label,
                )}
                levels={[]}
                span={null}
              />
            {/key}
          {:else}
            <p
              class="grid place-items-center border border-dashed border-border px-4 text-center text-sm text-pretty text-muted-foreground block-[min(24rem,50svh)]"
            >
              {m.map_rebuilding()}
            </p>
          {/if}
        {:else if race == null && view?.states != null}
          {const drawn = $derived(view.states)}
          {#key drawn.tiles}
            <PlaceMap
              places={drawn.places}
              isTally
              colors={drawn.places.legend.map(paint)}
              tiles={drawn.tiles}
              own={null}
              frame={drawn.frame}
              approximate={[]}
              noun={drawn.places.levels[0]?.noun ?? ""}
              votes={m.col_seats()}
              names={drawn.places.legend.map(
                (item) => item.short ?? item.label,
              )}
              levels={[]}
              span={null}
            />
          {/key}
        {:else if current?.isFailed === true}
          <div
            class="grid place-items-center gap-3 border border-dashed border-border px-4 py-6 text-center text-sm text-pretty text-muted-foreground"
            role="alert"
          >
            <p>{m.scrubber_failed({ year: String(step?.year ?? "") })}</p>
            <button
              type="button"
              class="rounded-sm border border-input bg-card px-3 text-[0.8125rem] font-semibold text-foreground block-8 hover:bg-secondary"
              onclick={() => {
                if (current != null) {
                  show(current.path);
                }
              }}>{m.search_retry()}</button
            >
          </div>
        {:else if current?.view == null}
          <Pending
            name="scrubber.map"
            isPending={true}
            reserve="min(80svh,130vw)"
            class="[--island-inset:0]"
          />
        {:else}
          <p
            class="grid place-items-center border border-dashed border-border px-4 text-center text-sm text-pretty text-muted-foreground block-24"
          >
            {m.scrubber_no_map({ year: String(step?.year ?? "") })}
          </p>
        {/if}
      </div>

      <div class="grid content-start gap-4">
        <div class="grid gap-1 max-lg:hidden">
          <p
            class="font-figures text-[clamp(3rem,7vw,5.5rem)] leading-[0.85] font-extrabold tracking-[-0.03em]"
            aria-live="polite"
          >
            {step?.year ?? ""}
          </p>
          {#if hub != null && step != null}
            <a
              class="text-[0.9375rem] font-semibold text-pretty underline decoration-border underline-offset-4 hover:decoration-foreground"
              href={resolve("/[...path]", { path: step.path })}>{hub.name}</a
            >
            {#if hub.coverage != null}
              <p class="text-[0.8125rem] font-semibold text-pretty">
                {m.election_partial({
                  held: count(hub.coverage.held),
                  of: count(hub.coverage.of),
                  noun: hub.coverage.noun,
                })}
              </p>
            {/if}
          {/if}
        </div>

        {#if race != null && balance?.parts.some((part) => part.value > 0) === true}
          <div class="grid gap-2">
            <p class="text-[0.8125rem] font-bold">{race.name}</p>
            <Balance {...balance} />
          </div>
        {/if}

        {#if chamber != null && sitting.length > 0}
          <Seats
            parties={sitting}
            places={[]}
            here={null}
            {place}
            link={null}
            size={null}
            chamber={chamber.id}
            title={standing?.path === step?.path
              ? m.scrubber_chamber({ chamber: chamber.name })
              : m.scrubber_chamber_standing({
                  chamber: chamber.name,
                  year: String(standing?.year ?? ""),
                })}
            isCompact
          />
        {/if}

        {#if government != null}
          <div class="grid gap-1">
            <p class="text-[0.8125rem] font-bold">{m.scrubber_government()}</p>
            <p class="flex items-baseline gap-1.5 text-sm">
              <span
                class="self-center rounded-xs block-2.5 inline-2.5"
                style:background-color={tint(government.party)}
              ></span>
              <span class="font-semibold">{government.name}</span>
            </p>
            {#if government.head != null}
              <p class="text-[0.8125rem] text-muted-foreground">
                <Name
                  label={government.head.name}
                  person={government.head.id}
                />
              </p>
            {/if}
          </div>
        {/if}
      </div>
    </div>
  </section>
{/if}
