<script lang="ts">
  import type {
    LiveElection,
    LiveEstimate,
    LiveRace,
  } from "@ea/analytics/live";
  import { untrack } from "svelte";
  import type { Holder } from "#lib/live/ballot.js";
  import type { Geography, Mode } from "#lib/live/standing.js";
  import BoardLink from "#lib/components/live/board-link.svelte";
  import Cycle from "#lib/components/live/cycle.svelte";
  import MapLink from "#lib/components/live/map-link.svelte";
  import { count, percent } from "#lib/format.js";
  import { shift } from "#lib/live/flips.js";
  import {
    bandFor,
    clock,
    isDistricted,
    lead,
    placeScheme,
    placeSlug,
    stamp,
    tone,
  } from "#lib/live/format.js";
  import { estimateAt, history } from "#lib/live/history.js";
  import { liveLinks } from "#lib/live/links.js";
  import { sides } from "#lib/live/local.js";
  import { standing, sum } from "#lib/live/standing.js";
  import { quiet } from "#lib/live/summary.js";
  import { line, terms } from "#lib/measure.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Answers from "./answers.svelte";
  import BandLabel from "./band-label.svelte";
  import Flip from "./dashboard/flip.svelte";
  import Dial from "./dial.svelte";
  import Feed from "./feed.svelte";
  import Field from "./field.svelte";
  import { isOutlying } from "./full/model.js";
  import Later from "./later.svelte";
  import LiveMap from "./map.svelte";
  import MethodLink from "./method-link.svelte";
  import Outstanding from "./outstanding.svelte";
  import Seats from "./seats.svelte";
  import Shares from "./shares.svelte";
  import Seat from "./state/seat.svelte";
  import Table from "./table.svelte";
  import Tote from "./tote.svelte";
  import Trend from "./trend.svelte";
  import Unopposed from "./unopposed.svelte";
  import Withheld from "./withheld.svelte";

  let {
    election,
    race,
    geography,
    seat = null,
    base = null,
    clock: time,
    at = $bindable(null),
    isOffline = false,
    isFinal = $bindable(true),
    forecasts = true,
  }: {
    election: LiveElection;
    race: LiveRace;
    geography: Geography | null;
    seat?: { held: Holder | null; families: Record<string, string> } | null;
    base?: string | null;
    clock: number;
    at?: string | null;
    isOffline?: boolean;
    isFinal?: boolean;
    forecasts?: boolean;
  } = $props();

  const toLive = liveLinks();

  const RECOUNT: Record<string, number> = { mi: 2000 };

  let mode = $state<Mode>("lead");
  const modes = $derived<Array<Mode>>(
    race.baseline.length > 0
      ? ["lead", "bubbles", "shift", "counted"]
      : ["lead", "bubbles", "counted"],
  );

  const counties = $derived(
    new Map(
      race.units.flatMap((unit) =>
        unit.scheme === placeScheme(race.state)
          ? [[unit.id, unit.name] as const]
          : [],
      ),
    ),
  );

  function href(unitId: string) {
    const name = counties.get(unitId);
    return base?.split("/").length !== 3 || name == null
      ? null
      : toLive(`${base}/place/${placeSlug(name)}`);
  }
  let selected = $state<string | null>(null);
  let hovered = $state<string | null>(null);
  let seen = new Set<string>();
  let withdrawn = $state<Array<string>>([]);
  let late = $state<Array<string>>([]);

  const view = $derived(
    at != null && race.frames.some((frame) => frame.at === at) ? at : null,
  );
  const now = $derived(
    standing(race, { at: view, isFinal: isFinal && view == null }),
  );
  const options = $derived(race.options);
  const isPlural = $derived(
    race.isMeasure === true &&
      options.filter((option) => !option.isWriteIn).length > 2,
  );
  const isModeled = $derived(forecasts && !isPlural);
  const ranking = $derived(
    now.total > 0
      ? now.order.filter((index) => options[index]?.isWriteIn !== true)
      : options.flatMap((option, index) => (option.isWriteIn ? [] : [index])),
  );
  const pair = $derived(now.pair);
  const mark = $derived.by(() => {
    const sided = race.isMeasure === true ? sides(race) : null;
    if (sided == null || race.rule == null) {
      return 0.5;
    }
    return sided.yes === pair[0] ? line(race.rule) : 1 - line(race.rule);
  });
  const modeled = $derived(
    now.isFinal
      ? estimateAt(race, null)
      : estimateAt({ ...race, certified: null }, view),
  );
  const estimate: LiveEstimate = $derived(
    isModeled
      ? modeled
      : {
          ...modeled,
          status: "unavailable",
          expectedTotal: {
            low: modeled.counted,
            mid: modeled.counted,
            high: modeled.counted,
          },
          margin: { low: 0, mid: 0, high: 0 },
          winProbability: {},
          units: [],
        },
  );
  const points = $derived(isModeled ? history(race, null) : []);
  const isUnestimated = $derived(isModeled && modeled.status === "unavailable");
  const seats = $derived(race.seats ?? 1);
  const isMulti = $derived(
    seats > 1 && !race.isMeasure && options.length > seats,
  );
  const isField = $derived.by(() => {
    if (race.isMeasure) {
      return false;
    }
    if (seats > 1) {
      return true;
    }
    const third = now.order[2];
    return (
      third != null &&
      now.total > 0 &&
      (now.totals[third] ?? 0) / now.total >= 0.1
    );
  });
  const upto = $derived(
    view == null ? points : points.filter((point) => point.at <= view),
  );
  const winners = $derived(now.isFinal ? (race.certified?.winners ?? []) : []);
  const projected = $derived(
    race.certified == null &&
      race.projected != null &&
      (view == null || race.projected.at <= view)
      ? (options.find((option) => option.id === race.projected?.option) ?? null)
      : null,
  );
  const lone = $derived(
    race.certified == null && projected == null ? race.unopposed : null,
  );
  const loneOptions = $derived(
    options.filter((option) => lone?.options.includes(option.id) === true),
  );
  const band = $derived(
    bandFor(
      options,
      lone?.options ?? (projected == null ? winners : [projected.id]),
      seats,
      projected != null,
      lone != null,
    ),
  );
  const closes = $derived(
    Date.parse(
      election.pollClose.find((entry) => entry.state === race.state)?.at ??
        `${election.date}T23:59:00Z`,
    ),
  );
  const opens = $derived(closes - 3_600_000);
  const flip = $derived.by(() => {
    const called =
      projected ??
      options.find((option) => winners.includes(option.id)) ??
      loneOptions[0] ??
      null;
    const ahead =
      called ?? (now.total > 0 ? (options[now.order[0] ?? -1] ?? null) : null);
    const party = ahead?.party ?? null;
    const found = shift({
      held: seat?.held?.id ?? null,
      to: party == null ? null : (seat?.families[party.id] ?? party.id),
      isCalled: called != null,
      isCounting: now.total > 0,
    });
    return ahead == null || found == null || found.flip === "held"
      ? null
      : {
          flip: found.flip,
          to: { short: party?.short ?? "", color: tone(ahead) },
        };
  });
  const isWaiting = $derived(now.total === 0);
  const isUncounted = $derived(lone != null && isWaiting);
  const later = $derived(
    isWaiting &&
      lone == null &&
      race.certified == null &&
      (race.feed === "none" || race.feed === "outside")
      ? race.feed
      : null,
  );
  const isLive = $derived(
    !isPlural && modeled.status === "estimating" && !now.isFinal,
  );
  const newest = $derived(race.frames.at(-1)?.at ?? null);
  const signed = $derived(
    now.total > 0
      ? ((now.totals[pair[0]] ?? 0) - (now.totals[pair[1]] ?? 0)) / now.total
      : null,
  );
  const votesLead = $derived(
    Math.abs((now.totals[pair[0]] ?? 0) - (now.totals[pair[1]] ?? 0)),
  );

  let last = -Infinity;

  $effect(() => {
    const stamps = race.frames.map((frame) => frame.at);
    const current = new Set(stamps);
    const tail = stamps.at(-1) ?? "";
    const moment = time;
    untrack(() => track(stamps, current, tail, moment));
  });

  function track(
    stamps: Array<string>,
    current: Set<string>,
    tail: string,
    moment: number,
  ) {
    const missing = [...seen].filter((value) => !current.has(value));
    if (moment < last || missing.length > 2) {
      withdrawn = withdrawn.filter(
        (value) => value < tail && !current.has(value),
      );
      late = late.filter((value) => current.has(value) && value <= tail);
      seen = current;
      last = moment;
      return;
    }
    last = moment;
    let latest = "";
    for (const value of seen) {
      if (value > latest) {
        latest = value;
      }
    }
    const gone = missing.filter((value) => !withdrawn.includes(value));
    const fresh =
      seen.size === 0
        ? []
        : stamps.filter(
            (value) =>
              !seen.has(value) && value < latest && !late.includes(value),
          );
    if (gone.length > 0) {
      withdrawn = [...withdrawn, ...gone].toSorted().slice(-3);
    }
    if (fresh.length > 0) {
      late = [...late, ...fresh].toSorted().slice(-3);
    }
    seen = current;
  }

  const status = $derived.by(() => {
    if (race.certified != null) {
      return "certified" as const;
    }
    if (isUncounted) {
      return "unopposed" as const;
    }
    if (later != null) {
      return "later" as const;
    }
    if (isOffline) {
      return "offline" as const;
    }
    if (time < closes && now.total === 0) {
      return "before" as const;
    }
    return "live" as const;
  });

  const headline = $derived.by(() => {
    const leader = options[now.order[0] ?? 0];
    if (isMulti && now.total > 0 && later == null) {
      const last = now.order[seats - 1] ?? 0;
      const next = now.order[seats] ?? 0;
      const values = {
        seats: count(seats),
        name: options[last]?.label ?? "",
        next: options[next]?.label ?? "",
        lead: count((now.totals[last] ?? 0) - (now.totals[next] ?? 0)),
      };
      return now.isFinal && race.certified != null
        ? m.night_head_seats_filled(values)
        : m.night_head_seats_holding(values);
    }
    if (race.unopposed != null && seats > 1) {
      return m.night_head_seats_unopposed({
        seats: count(seats),
        count: count(race.unopposed.options.length),
      });
    }
    if (lone != null) {
      return m.night_head_unopposed({ name: loneOptions[0]?.label ?? "" });
    }
    if (isPlural && now.total > 0 && later == null && projected == null) {
      const first = ranking[0] ?? 0;
      const second = ranking[1] ?? 0;
      const gap =
        ((now.totals[first] ?? 0) - (now.totals[second] ?? 0)) / now.total;
      const values = {
        name: options[first]?.label ?? "",
        next: options[second]?.label ?? "",
        margin: lead(gap),
      };
      if (now.isFinal && race.certified != null) {
        return m.night_head_answers_won({
          ...values,
          name:
            options.find((option) => winners.includes(option.id))?.label ??
            values.name,
        });
      }
      return gap < 0.0005
        ? m.night_head_tied({ count: count(now.total) })
        : m.night_head_answers({ ...values, count: count(now.total) });
    }
    if (now.isFinal && race.certified != null) {
      const winner = options.find((option) => winners.includes(option.id));
      return m.night_head_won({
        name: winner?.label ?? leader?.label ?? "",
        margin: lead(Math.abs(signed ?? 0)),
      });
    }
    if (projected != null) {
      return m.night_head_projected({
        name: projected.label,
        time: stamp(race.projected?.at ?? null),
      });
    }
    if (later != null) {
      return m.night_head_later();
    }
    if (now.total === 0) {
      return time < closes
        ? m.night_head_before({ time: stamp(closes) })
        : m.night_head_waiting();
    }
    const name = leader?.label ?? "";
    if (Math.abs(signed ?? 0) < 0.0005) {
      return m.night_head_tied({ count: count(now.total) });
    }
    if (estimate.status === "estimating") {
      return m.night_head_estimating({
        name,
        margin: lead(Math.abs(signed ?? 0)),
        share: percent(Math.min(0.99, estimate.shareIn.mid), 0),
      });
    }
    return m.night_head_counting({
      name,
      margin: lead(Math.abs(signed ?? 0)),
      count: count(now.total),
    });
  });

  const notes = $derived.by(() => {
    const list: Array<{ id: string; text: string; isAlert: boolean }> = [];
    if (isOffline) {
      list.push({
        id: "offline",
        text: m.night_note_offline({ time: stamp(newest) }),
        isAlert: true,
      });
    }
    if (view != null) {
      list.push({
        id: "past",
        text: m.night_note_past({ time: stamp(view) }),
        isAlert: false,
      });
    }
    for (const value of withdrawn) {
      list.push({
        id: `withdrawn-${value}`,
        text: m.night_note_withdrawn({ time: clock(value) }),
        isAlert: false,
      });
    }
    for (const value of late) {
      list.push({
        id: `late-${value}`,
        text: m.night_note_late({ time: clock(value) }),
        isAlert: false,
      });
    }
    const last = race.frames.at(-1);
    const before = race.frames.at(-2);
    if (
      view == null &&
      last != null &&
      before != null &&
      sum(last.totals) < sum(before.totals)
    ) {
      list.push({
        id: "correction",
        text: m.night_note_correction({
          count: count(sum(before.totals) - sum(last.totals)),
        }),
        isAlert: false,
      });
    }
    for (const row of now.units.values()) {
      if (
        row.reporting != null &&
        row.expected != null &&
        row.expected > 0 &&
        row.reporting > row.expected
      ) {
        list.push({
          id: `over-${row.unit.id}`,
          text: m.night_note_over({ name: row.unit.name }),
          isAlert: false,
        });
      }
    }
    if (
      race.certified == null &&
      view == null &&
      newest != null &&
      time > closes &&
      time - Date.parse(newest) > 45 * 60_000 &&
      estimate.shareIn.mid < 0.99
    ) {
      list.push({
        id: "stale",
        text: quiet(Math.round((time - Date.parse(newest)) / 60_000)),
        isAlert: false,
      });
    }
    const limit = RECOUNT[race.state];
    if (now.total > 0 && estimate.shareIn.mid >= 0.9) {
      if (limit != null && votesLead <= limit) {
        list.push({
          id: "recount",
          text: m.night_note_recount({ count: count(limit) }),
          isAlert: true,
        });
      } else if (limit == null && Math.abs(signed ?? 1) <= 0.005) {
        list.push({ id: "close", text: m.night_note_close(), isAlert: true });
      }
    }
    if (race.certified != null && !now.isFinal) {
      list.push({
        id: "certified",
        text: m.night_note_certified(),
        isAlert: false,
      });
    }
    return list;
  });

  function pick(value: string | null) {
    if (value == null || value === newest) {
      at = null;
      isFinal = true;
    } else {
      at = value;
    }
  }
</script>

<article class="grid gap-px bg-border">
  <header class="grid gap-2 bg-background px-4 pbs-4 pbe-4 sm:px-8">
    <p
      class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground"
    >
      <span
        class={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold",
          {
            "bg-foreground text-background": status === "live",
            "border border-border":
              status === "before" ||
              status === "certified" ||
              status === "unopposed" ||
              status === "later",
            "bg-(--no) text-background": status === "offline",
          },
        )}
      >
        {#if status === "live"}
          <span
            class="animate-pulse rounded-full bg-background block-1.5 inline-1.5 motion-reduce:animate-none"
          ></span>
          {m.night_status_live()}
        {:else if status === "before"}
          {m.night_status_before()}
        {:else if status === "certified"}
          {m.night_status_certified()}
        {:else if status === "unopposed"}
          {m.night_state_unopposed()}
        {:else if status === "later"}
          {m.night_later()}
        {:else}
          {m.night_status_offline()}
        {/if}
      </span>
      {#if newest != null}
        <span class="font-figures tabular-nums"
          >{m.night_updated({ time: stamp(view ?? newest) })}</span
        >
      {/if}
      {#if seat != null}
        <Seat held={seat.held} />
        {#if flip != null}
          <Flip
            flip={flip.flip}
            from={seat.held}
            to={flip.to}
            isLabeled={true}
          />
        {/if}
      {/if}
      <BoardLink election={election.slug} race={race.slug} />
      <MapLink election={election.slug} state={race.state} race={race.slug} />
    </p>
    <h1 class="title text-balance">{race.title}</h1>
    <p
      class="font-serif text-lg/snug text-pretty max-inline-[72ch]"
      aria-live="polite"
    >
      {headline}
    </p>
    {#if notes.length > 0}
      <ul class="grid gap-1">
        {#each notes as note (note.id)}
          <li
            class={cn("text-[0.875rem] max-inline-[68ch]", {
              "font-semibold": note.isAlert,
              "text-muted-foreground": !note.isAlert,
            })}
          >
            {note.text}
            {#if note.id === "past"}
              <button
                type="button"
                class="ms-1 font-semibold text-foreground underline underline-offset-2"
                onclick={() => pick(null)}
              >
                {m.night_note_latest()}
              </button>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </header>

  {#snippet result()}
    <section class="bg-background" style:--party={band?.fill}>
      <div
        class={cn(
          "grid gap-5 px-4 py-5 transition-colors duration-700 motion-reduce:transition-none sm:px-8",
          { band: band != null },
        )}
      >
        {#if band != null}
          <BandLabel
            {band}
            isMeasure={race.isMeasure ?? false}
            at={race.projected?.at ?? null}
            class="text-[0.8125rem]"
          />
        {/if}
        {#if isField}
          <Seats {options} {now} {seats} {band} />
        {:else if isPlural}
          <Answers
            {options}
            {now}
            {estimate}
            {band}
            {isWaiting}
            isLater={later != null}
          />
        {:else}
          <Tote
            {options}
            {now}
            {estimate}
            {band}
            {isWaiting}
            isLater={later != null}
            {mark}
            needs={terms(race.rule)}
          />
        {/if}
      </div>
    </section>
  {/snippet}

  {#if later != null && band != null}
    {@render result()}
  {/if}

  {#if later != null}
    <section class="bg-background px-4 py-6 sm:px-8">
      {const state = $derived(
        election.later?.[race.state]?.name ?? race.state.toUpperCase(),
      )}
      <Later
        text={later === "none"
          ? m.night_later_body({ state })
          : m.night_later_body_missing({ state })}
        {closes}
        {time}
      />
    </section>
  {/if}

  {#if isUncounted}
    <section class="bg-background px-4 py-6 sm:px-8">
      <Unopposed
        options={loneOptions}
        isTacit={lone?.isTacit === true}
        {seats}
      />
    </section>
  {:else if later == null || band == null}
    {@render result()}
  {/if}

  {#if !isUncounted && later == null && isMulti}
    <section class="bg-background px-4 py-6 sm:px-8">
      <Feed {options} {pair} frames={race.frames} at={view} onPick={pick} />
    </section>
  {:else if !isUncounted && later == null}
    {#if isLive && !isUnestimated}
      <section
        class="grid gap-8 bg-background px-4 py-5 sm:px-8 lg:grid-cols-2"
      >
        <LiveMap
          {geography}
          isAlone={isOutlying(race.state)}
          {options}
          {now}
          {estimate}
          {modes}
          title={m.night_map_counted_title()}
          bind:mode
          {href}
          bind:selected
          bind:hovered
        />
        <Withheld isOff={!forecasts}>
          <LiveMap
            {geography}
            isAlone={isOutlying(race.state)}
            {options}
            {now}
            {estimate}
            modes={["remaining"]}
            title={m.night_map_left_title()}
            caption={m.night_map_left_caption()}
            help="turnout"
            mode="remaining"
            {href}
            bind:selected
            bind:hovered
          />
        </Withheld>
      </section>
    {:else}
      <section
        class="grid gap-8 bg-background px-4 py-5 sm:px-8 lg:grid-cols-[minmax(0,1fr)_24rem] lg:gap-10"
      >
        <LiveMap
          {geography}
          isAlone={isOutlying(race.state)}
          {options}
          {now}
          {estimate}
          {modes}
          answers={isPlural ? ranking : null}
          title={m.night_map_counted_title()}
          bind:mode
          {href}
          bind:selected
          bind:hovered
        />
        <Feed {options} {pair} frames={race.frames} at={view} onPick={pick} />
      </section>
    {/if}

    {#if isUnestimated && (race.certified == null || !now.isFinal)}
      <section class="grid gap-1 bg-background px-4 py-5 sm:px-8">
        <div class="flex items-center gap-1">
          <h2 class="text-[0.8125rem] font-semibold">
            {m.night_dial_none_title()}
          </h2>
          <MethodLink section="limits" />
        </div>
        <p class="text-sm text-muted-foreground max-inline-[60ch]">
          {m.night_dial_none_body()}
        </p>
      </section>
    {:else if !isPlural && (race.certified == null || !now.isFinal)}
      <section class="grid gap-10 bg-background px-4 py-6 sm:px-8">
        <Withheld isOff={!forecasts}>
          <Dial
            {options}
            {pair}
            {estimate}
            history={upto}
            counted={signed}
            {isWaiting}
          />
        </Withheld>
        {#if isLive}
          <Withheld isOff={!forecasts}>
            <Field {options} {now} {estimate} {winners} />
          </Withheld>
        {/if}
      </section>
    {/if}

    {#if !race.isMeasure}
      <Cycle
        path={race.slug}
        {options}
        totals={now.totals}
        total={now.total}
        counted={estimate.status === "unavailable"
          ? null
          : Math.min(1, estimate.shareIn.mid)}
        estimate={isModeled && !now.isFinal ? estimate : null}
        class="bg-background px-4 py-6 sm:px-8"
      />
    {/if}

    <section class="bg-background px-4 py-6 sm:px-8">
      {#if isPlural}
        <Shares
          {options}
          order={ranking}
          frames={race.frames}
          at={view}
          {opens}
          onPick={pick}
        />
      {:else}
        <Trend
          {options}
          {pair}
          frames={race.frames}
          {points}
          expected={estimate.status === "estimating"
            ? estimate.expectedTotal.mid
            : null}
          at={view}
          {opens}
          onPick={pick}
        />
      {/if}
    </section>

    {#if isLive && !isUnestimated}
      <section
        class="grid gap-8 bg-background px-4 py-6 sm:px-8 lg:grid-cols-[minmax(0,1fr)_24rem] lg:gap-10"
      >
        <Withheld isOff={!forecasts}>
          <Outstanding
            {options}
            {now}
            {estimate}
            {href}
            bind:selected
            bind:hovered
          />
        </Withheld>
        <Feed {options} {pair} frames={race.frames} at={view} onPick={pick} />
      </section>
    {/if}

    <section class="bg-background px-4 py-6 sm:px-8">
      <Table
        isDistrict={isDistricted(race.state)}
        {options}
        {now}
        {estimate}
        answers={isPlural ? ranking : null}
        {href}
        bind:selected
        bind:hovered
      />
    </section>
  {/if}

  <footer
    class="grid gap-1 bg-background px-4 py-5 text-xs text-muted-foreground sm:px-8"
  >
    {#if later == null && !isUncounted}
      <p class="max-inline-[80ch]">
        {isDistricted(race.state)
          ? m.night_method_district()
          : m.night_method()}
      </p>
    {/if}
    {#if race.source.publisher !== ""}
      <p>
        {m.night_source()}
        {#if race.source.url != null}
          <a
            href={race.source.url}
            class="underline underline-offset-2"
            rel="external noopener">{race.source.publisher}</a
          >
        {:else}
          {race.source.publisher}
        {/if}
      </p>
    {/if}
    {#if race.placeSource != null}
      <p>
        {m.night_source_places()}
        {#if race.placeSource.url != null}
          <a
            href={race.placeSource.url}
            class="underline underline-offset-2"
            rel="external noopener">{race.placeSource.publisher}</a
          >
        {:else}
          {race.placeSource.publisher}
        {/if}
      </p>
    {/if}
  </footer>
</article>
