<script lang="ts">
  import type {
    LiveElection,
    LiveEstimate,
    LiveOption,
  } from "@ea/analytics/live";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { navigating, page } from "$app/state";
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import LoaderCircleIcon from "@lucide/svelte/icons/loader-circle";
  import MapIcon from "@lucide/svelte/icons/map";
  import MinusIcon from "@lucide/svelte/icons/minus";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import ScanIcon from "@lucide/svelte/icons/scan";
  import XIcon from "@lucide/svelte/icons/x";
  import { untrack } from "svelte";
  import { MediaQuery } from "svelte/reactivity";
  import type { SegmentedOption } from "#lib/components/ui/segmented/index.js";
  import type { Control } from "#lib/live/nation.js";
  import type { Geography, UnitStanding } from "#lib/live/standing.js";
  import type { Summary } from "#lib/live/summary.js";
  import Drawer from "#lib/atlas/drawer.svelte";
  import Monogram from "#lib/components/monogram.svelte";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { percent } from "#lib/format.js";
  import {
    lead,
    placeName,
    placeSlug,
    stamp,
    surname,
    tone,
  } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import { prefs } from "#lib/live/prefs.svelte.js";
  import { pair as pairOf, remainder, standing } from "#lib/live/standing.js";
  import { closing, describe, verdict } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { Dot, Fill, Lens, Office, Paint } from "./model.js";
  import Canvas from "./canvas.svelte";
  import CountyCard from "./county-card.svelte";
  import CountyList from "./county-list.svelte";
  import Legend from "./legend.svelte";
  import {
    BLANK,
    board,
    districtKey,
    flipped,
    holding,
    LENSES,
    MUTED,
    OFFICES,
    PAINTS,
    prior,
    seatName,
    shade,
    stateShade,
    tail,
    unitFill,
    urgency,
  } from "./model.js";
  import NationPanel from "./nation-panel.svelte";
  import RaceCard from "./race-card.svelte";

  const {
    election,
    estimates,
    nation,
    control,
    districts,
    geography,
    nationPath,
    names,
    clock,
    isOffline,
    forecasts,
  }: {
    election: LiveElection;
    estimates: Record<string, LiveEstimate>;
    nation: {
      map: Geography;
      codes: Record<string, string>;
      labels: Record<string, string>;
    } | null;
    control: Control | null;
    districts: { map: Geography; codes: Record<string, string> } | null;
    geography: Geography | null;
    nationPath: string;
    names: Record<string, string>;
    clock: number;
    isOffline: boolean;
    forecasts: boolean;
  } = $props();

  const VIEW = { at: null, isFinal: true };
  const wide = new MediaQuery("min-width: 48rem", false);
  const toLive = liveLinks();

  let canvas = $state<{
    focus: (areaId: string) => void;
    fitTo: (areaIds: Array<string>) => boolean;
    zoomIn: () => void;
    zoomOut: () => void;
    reset: () => void;
  } | null>(null);
  let hovered = $state<string | null>(null);
  let detent = $state<"peek" | "half" | "full">(
    untrack(() =>
      page.url.searchParams.has("pick") || page.url.searchParams.has("county")
        ? "half"
        : "peek",
    ),
  );
  let sheet = $state(0);
  let room = $state(0);
  let controlsHeight = $state(0);
  let pulses = $state<Array<{ key: string; areaId: string; color: string }>>(
    [],
  );
  let fresh = $state<Array<string>>([]);

  const isNation = $derived(nation != null);
  const date = $derived(election.slug.split("/").at(-1) ?? "");
  const code = $derived(
    isNation ? null : (election.slug.split("/")[1] ?? null),
  );
  const found = $derived(board(election, estimates, control));
  const params = $derived(page.url.searchParams);

  function stateName(value: string) {
    return names[value] ?? value.toUpperCase();
  }

  const here = $derived(code == null ? m.livemap_nation() : stateName(code));

  const offices = $derived(
    OFFICES.filter((office) =>
      found.summaries.some((summary) => summary.race.office === office),
    ),
  );
  const office = $derived.by((): Office => {
    const wanted = params.get("office");
    const match = offices.find((entry) => entry === wanted);
    return match ?? offices[0] ?? "senate";
  });
  const paint = $derived.by((): Paint => {
    const wanted = params.get("color");
    const match = PAINTS.find(
      (entry) => entry === wanted && (forecasts || entry !== "chance"),
    );
    return match ?? "lead";
  });
  const pick = $derived(isNation ? params.get("pick") : null);

  const statewide = $derived(
    found.summaries
      .filter(
        (summary) =>
          summary.race.office !== "house" && summary.race.units.length > 0,
      )
      .toSorted(
        (a, b) =>
          OFFICES.indexOf(a.race.office as Office) -
            OFFICES.indexOf(b.race.office as Office) ||
          a.race.title.localeCompare(b.race.title),
      ),
  );
  const seats = $derived(
    found.summaries
      .filter((summary) => summary.race.office === "house")
      .toSorted(
        (a, b) =>
          Number.parseInt(a.race.district ?? "0", 10) -
          Number.parseInt(b.race.district ?? "0", 10),
      ),
  );
  const hasOverview = $derived(
    !isNation && districts != null && seats.length > 1,
  );
  const raceTail = $derived.by(() => {
    const wanted = params.get("race");
    if (wanted === "house" && hasOverview) {
      return "house";
    }
    if (
      wanted != null &&
      found.summaries.some((summary) => tail(summary.race) === wanted)
    ) {
      return wanted;
    }
    const first = statewide[0] ?? seats[0];
    if (first == null) {
      return hasOverview ? "house" : "";
    }
    return statewide.length === 0 && hasOverview ? "house" : tail(first.race);
  });
  const isOverview = $derived(!isNation && raceTail === "house");
  const current = $derived<Summary | null>(
    isNation || isOverview
      ? null
      : (found.summaries.find((summary) => tail(summary.race) === raceTail) ??
          null),
  );
  const race = $derived(current?.race ?? null);
  const raceStanding = $derived(race == null ? null : standing(race, VIEW));
  const racePair = $derived<[number, number]>(
    race == null ? [0, 1] : pairOf(race),
  );
  const estimate = $derived(race == null ? null : (estimates[race.id] ?? null));
  const hasVotes = $derived((current?.total ?? 0) > 0);
  const lens = $derived.by((): Lens => {
    const wanted = params.get("color");
    const match = LENSES.find((entry) => entry === wanted);
    if (isOverview) {
      return match === "counted" ? "counted" : "lead";
    }
    if (match != null && (hasVotes || match === "last")) {
      return match;
    }
    return hasVotes ? "lead" : "last";
  });

  const counties = $derived.by(() => {
    const byArea = new Map<
      string,
      { areaId: string; name: string; slug: string }
    >();
    for (const summary of found.summaries) {
      for (const unit of summary.race.units) {
        if (unit.scheme === "us.county" && unit.areaId != null) {
          byArea.set(unit.areaId, {
            areaId: unit.areaId,
            name: placeName(unit.name),
            slug: placeSlug(unit.name),
          });
        }
      }
    }
    return byArea;
  });
  const bySlug = $derived(
    new Map([...counties.values()].map((entry) => [entry.slug, entry])),
  );
  const county = $derived(
    isNation ? null : (bySlug.get(params.get("county") ?? "") ?? null),
  );

  const rows = $derived.by(() => {
    if (race == null || raceStanding == null) {
      return [];
    }
    const out: Array<{
      areaId: string;
      name: string;
      row: UnitStanding;
      counted: number | null;
      color: string;
    }> = [];
    for (const unit of race.units) {
      const row = raceStanding.units.get(unit.id);
      if (row == null || unit.areaId == null || unit.scheme !== "us.county") {
        continue;
      }
      const rest = remainder(row, estimate, race.options, racePair);
      out.push({
        areaId: unit.areaId,
        name: placeName(unit.name),
        row,
        counted:
          row.total > 0
            ? Math.min(1, row.total / Math.max(1, rest.expected))
            : null,
        color: unitFill(row, lens, race.options, racePair, rest.expected),
      });
    }
    return out;
  });
  const rowByArea = $derived(
    new Map(rows.map((entry) => [entry.areaId, entry])),
  );

  const nationCodes = $derived(nation?.codes ?? {});
  const districtCodes = $derived(districts?.codes ?? {});
  const isHouseMap = $derived(
    isNation && office === "house" && districts != null,
  );
  const keyOfArea = $derived(
    new Map(
      Object.entries(
        isHouseMap || isOverview ? districtCodes : nationCodes,
      ).map(([key, areaId]) => [areaId, key]),
    ),
  );
  const areaOfKey = $derived(
    isHouseMap || isOverview ? districtCodes : nationCodes,
  );

  const mapGeography = $derived(
    isNation
      ? isHouseMap
        ? (districts?.map ?? null)
        : (nation?.map ?? null)
      : isOverview
        ? (districts?.map ?? null)
        : geography,
  );

  function seatShade(summary: Summary, wanted: Paint) {
    return shade(
      summary,
      found.seats.get(summary.race.id),
      wanted,
      found.pair,
      control,
    );
  }

  const fills = $derived.by(() => {
    const out = new Map<string, Fill>();
    if (isNation && !isHouseMap) {
      for (const [key, areaId] of Object.entries(nationCodes)) {
        const races = (found.byState.get(key) ?? []).filter(
          (summary) => summary.race.office === office,
        );
        out.set(areaId, stateShade(races, office, paint, found, control));
      }
      return out;
    }
    if (isHouseMap || isOverview) {
      const wanted: Paint = isOverview
        ? lens === "counted"
          ? "counted"
          : "lead"
        : paint;
      for (const [key, areaId] of Object.entries(districtCodes)) {
        const summary = found.districts.get(key);
        out.set(
          areaId,
          summary == null
            ? { color: MUTED }
            : {
                color: seatShade(summary, wanted),
                isLater: summary.later != null,
              },
        );
      }
      return out;
    }
    const isLater = current?.later != null;
    for (const place of geography?.places ?? []) {
      const entry = rowByArea.get(place.areaId);
      out.set(
        place.areaId,
        entry == null
          ? { color: MUTED }
          : { color: lens === "votes" ? BLANK : entry.color, isLater },
      );
    }
    return out;
  });

  const dots = $derived.by((): Array<Dot> => {
    if (isNation || isOverview || race == null || lens !== "votes") {
      return [];
    }
    return rows.flatMap((entry) =>
      entry.row.total > 0 && entry.row.leader != null
        ? [
            {
              areaId: entry.areaId,
              value: entry.row.total,
              color: tone(race.options[entry.row.leader]),
            },
          ]
        : [],
    );
  });

  const labels = $derived.by(() => {
    if (isNation) {
      return isHouseMap
        ? []
        : Object.entries(nationCodes).map(([key, areaId], index) => ({
            areaId,
            text: stateName(key),
            rank: index,
          }));
    }
    if (isOverview) {
      return Object.entries(districtCodes).map(([key, areaId], index) => ({
        areaId,
        text: key.toUpperCase(),
        rank: index,
      }));
    }
    return rows
      .toSorted(
        (a, b) =>
          (b.row.baseline?.turnout ?? b.row.total) -
          (a.row.baseline?.turnout ?? a.row.total),
      )
      .map((entry, index) => ({
        areaId: entry.areaId,
        text: entry.name.replace(/ (County|Parish|Borough|Census Area)$/u, ""),
        rank: index,
      }));
  });

  const selected = $derived(
    isNation
      ? pick == null
        ? null
        : (areaOfKey[pick] ??
          (pick.includes("-") ? null : (nationCodes[pick] ?? null)))
      : (county?.areaId ?? null),
  );

  function href(path: string, next: Record<string, string | null>) {
    const url = new URL(
      resolve("/live/map/[...path]", { path }),
      page.url.origin,
    );
    for (const [key, value] of Object.entries(next)) {
      if (value != null && value !== "") {
        url.searchParams.set(key, value);
      }
    }
    return `${url.pathname}${url.search}`;
  }

  function update(next: Record<string, string | null>, isPushed = true) {
    const url = new URL(page.url.href);
    for (const [key, value] of Object.entries(next)) {
      if (value == null || value === "") {
        url.searchParams.delete(key);
      } else {
        url.searchParams.set(key, value);
      }
    }
    if (url.search === page.url.search) {
      return;
    }
    void goto(url, { reset: false, replace: !isPushed });
  }

  function open(target: string) {
    const areaId = nationCodes[target];
    if (areaId != null) {
      canvas?.focus(areaId);
    }
    const district =
      office === "house" && pick?.startsWith(`${target}-`) === true
        ? found.districts.get(pick)
        : null;
    void goto(
      href(`${election.slug.split("/")[0] ?? "us"}/${target}/${date}`, {
        race:
          district == null
            ? office === "house"
              ? "house"
              : office
            : tail(district.race),
        color: paint === "counted" ? "counted" : null,
      }),
      { reset: false },
    );
  }

  const up = $derived(
    code == null
      ? null
      : href(nationPath, {
          office:
            race?.office === "house" || isOverview
              ? "house"
              : race?.office === "governor"
                ? "governor"
                : null,
          pick: race?.office === "house" ? (districtKey(race) ?? code) : code,
        }),
  );

  function pickArea(areaId: string | null) {
    if (isNation) {
      const key = areaId == null ? null : (keyOfArea.get(areaId) ?? null);
      if (key == null) {
        update({ pick: null });
        return;
      }
      if (key === pick) {
        open(key.split("-")[0] ?? key);
        return;
      }
      update({ pick: key });
      if (!wide.current && detent === "peek") {
        detent = "half";
      }
      return;
    }
    if (isOverview) {
      const key = areaId == null ? null : keyOfArea.get(areaId);
      const summary = key == null ? null : found.districts.get(key);
      if (summary != null) {
        update({ race: tail(summary.race), county: null });
      }
      return;
    }
    const entry = areaId == null ? null : counties.get(areaId);
    update({ county: entry?.slug ?? null });
    if (entry != null && !wide.current && detent === "peek") {
      detent = "half";
    }
  }

  function pickRace(next: string) {
    update({ race: next, county: county?.slug ?? null });
  }

  let fitted = "";
  $effect(() => {
    const key = `${election.slug}|${raceTail}`;
    const target = race;
    const box = canvas;
    if (box == null || key === fitted || isNation) {
      return;
    }
    const isFirst = fitted === "" || !fitted.startsWith(`${election.slug}|`);
    fitted = key;
    const areas =
      target?.office === "house"
        ? target.units.flatMap((unit) =>
            unit.areaId == null ? [] : [unit.areaId],
          )
        : [];
    if (areas.length === 0) {
      if (!isFirst) {
        box.reset();
      }
      return;
    }
    let tries = 0;
    const attempt = () => {
      tries += 1;
      if (!box.fitTo(areas) && tries < 12) {
        timer = setTimeout(attempt, 350);
      }
    };
    let timer = setTimeout(attempt, isFirst ? 600 : 0);
    return () => clearTimeout(timer);
  });

  let seen: { stamp: string; totals: Map<string, number> } | null = null;
  $effect(() => {
    const newest = election.newest ?? "";
    const totals = new Map<string, number>();
    const names = new Map<string, string>();
    const colors = new Map<string, string>();
    if (isNation) {
      for (const summary of found.summaries) {
        const areaId = nationCodes[summary.race.state];
        if (areaId == null) {
          continue;
        }
        totals.set(areaId, (totals.get(areaId) ?? 0) + summary.total);
        names.set(areaId, stateName(summary.race.state));
        const leader = summary.leader;
        if (leader != null && summary.race.office === office) {
          colors.set(areaId, tone(leader));
        }
      }
    } else {
      for (const entry of untrack(() => rows)) {
        totals.set(entry.areaId, entry.row.total);
        names.set(entry.areaId, entry.name);
        const leader = entry.row.leader;
        if (leader != null && race != null) {
          colors.set(entry.areaId, tone(race.options[leader]));
        }
      }
    }
    const before = untrack(() => seen);
    seen = { stamp: newest, totals };
    if (before == null || before.stamp === newest) {
      return;
    }
    const grew = [...totals].filter(
      ([areaId, total]) => total > (before.totals.get(areaId) ?? 0),
    );
    if (grew.length === 0) {
      return;
    }
    pulses = grew
      .slice(0, 40)
      .map(([areaId]) => ({
        key: `${areaId}-${newest}`,
        areaId,
        color: colors.get(areaId) ?? "var(--foreground)",
      }));
    fresh = grew
      .toSorted(
        (a, b) =>
          b[1] -
          (before.totals.get(b[0]) ?? 0) -
          (a[1] - (before.totals.get(a[0]) ?? 0)),
      )
      .slice(0, 3)
      .flatMap(([areaId]) => {
        const name = names.get(areaId);
        return name == null ? [] : [name];
      });
    const timer = setTimeout(() => {
      pulses = [];
      fresh = [];
    }, 8000);
    return () => clearTimeout(timer);
  });

  const status = $derived(
    isOffline
      ? { label: m.night_status_offline(), isLive: false }
      : election.isCertified
        ? { label: m.night_status_certified(), isLive: false }
        : election.newest == null
          ? { label: m.night_status_before(), isLive: false }
          : { label: m.night_status_live(), isLive: true },
  );

  const OFFICE_NAMES: Record<Office, () => string> = {
    senate: m.night_nation_senate,
    governor: m.night_nation_governor,
    house: m.night_nation_house,
  };
  const officeOptions = $derived<Array<SegmentedOption<Office>>>(
    offices.map((value) => ({ value, label: OFFICE_NAMES[value]() })),
  );
  const paintOptions = $derived<Array<SegmentedOption<Paint>>>(
    (
      [
        ["lead", m.night_color_lead],
        ["flips", m.night_color_flips],
        ["counted", m.night_color_counted],
        ["chance", m.night_color_chance],
      ] as const
    )
      .filter(([value]) => forecasts || value !== "chance")
      .map(([value, label]) => ({ value, label: label() })),
  );
  const lensOptions = $derived<Array<SegmentedOption<Lens>>>(
    (
      [
        ["lead", m.night_map_lead],
        ["shift", m.night_map_shift],
        ["last", m.night_table_base],
        ["counted", m.night_map_counted],
        ["votes", m.night_map_bubbles],
      ] as const
    )
      .filter(
        ([value]) => !isOverview || value === "lead" || value === "counted",
      )
      .map(([value, label]) => ({
        value,
        label: label(),
        disabled: !hasVotes && !isOverview && value !== "last",
      })),
  );
  const raceOptions = $derived<Array<SegmentedOption<string>>>([
    ...statewide.map((summary) => ({
      value: tail(summary.race),
      label:
        summary.race.office === "senate" || summary.race.office === "governor"
          ? OFFICE_NAMES[summary.race.office]()
          : summary.race.title,
    })),
    ...(seats.length > 0
      ? [{ value: "house", label: m.night_nation_house() }]
      : []),
  ]);
  const raceGroup = $derived(
    race?.office === "house" || isOverview ? "house" : raceTail,
  );

  const sides = $derived.by(() => {
    if (isNation || isOverview) {
      return (found.pair ?? []).map((family) => ({
        name: family.short,
        color: family.color,
      }));
    }
    if (race == null) {
      return [];
    }
    return racePair.flatMap((index) => {
      const option = race.options[index];
      return option == null
        ? []
        : [{ name: surname(option), color: tone(option) }];
    });
  });
  const legendKind = $derived.by((): "ramp" | "counted" | "votes" => {
    const value = isNation ? paint : lens;
    return value === "counted"
      ? "counted"
      : value === "votes"
        ? "votes"
        : "ramp";
  });
  const legendText = $derived.by(() => {
    if (isNation) {
      return {
        lead: m.night_nation_legend,
        chance: m.night_nation_legend_chance,
        flips: m.night_nation_legend_flips,
        counted: m.night_nation_legend_reporting,
      }[paint]();
    }
    return {
      lead: m.night_legend_lead,
      shift: m.night_legend_shift,
      last: m.livemap_legend_last,
      counted: m.night_legend_counted,
      votes: m.night_legend_bubbles,
    }[lens]();
  });
  const isLaterShown = $derived(
    [...fills.values()].some((fill) => fill.isLater === true),
  );

  const others = $derived.by(() => {
    if (county == null) {
      return [];
    }
    return found.summaries.flatMap((summary) => {
      if (summary.race.id === race?.id) {
        return [];
      }
      const unit = summary.race.units.find(
        (entry) => entry.areaId === county.areaId,
      );
      const row =
        unit == null ? null : standing(summary.race, VIEW).units.get(unit.id);
      return row == null
        ? []
        : [
            {
              race: summary.race,
              row,
              tail: tail(summary.race),
              label:
                summary.race.office === "house"
                  ? seatName(summary.race, stateName)
                  : summary.race.office === "senate" ||
                      summary.race.office === "governor"
                    ? OFFICE_NAMES[summary.race.office]()
                    : summary.race.title,
            },
          ];
    });
  });
  const countyRow = $derived(
    county == null ? null : (rowByArea.get(county.areaId)?.row ?? null),
  );

  const loading = $derived.by(() => {
    const to = navigating.to;
    if (
      to?.route.id !== "/live/map/[...path]" ||
      to.url.pathname === page.url.pathname
    ) {
      return null;
    }
    const segments = to.params?.path?.split("/") ?? [];
    return segments.length === 2
      ? m.livemap_nation()
      : stateName(segments[1] ?? "");
  });

  function tipRace(areaId: string) {
    if (isNation || isOverview) {
      const key = keyOfArea.get(areaId);
      if (key == null) {
        return null;
      }
      if (isHouseMap || isOverview) {
        const summary = found.districts.get(key);
        return summary == null
          ? { title: key.toUpperCase(), lines: [] as Array<Summary> }
          : { title: seatName(summary.race, stateName), lines: [summary] };
      }
      return {
        title: stateName(key),
        lines: (found.byState.get(key) ?? []).filter(
          (summary) => summary.race.office === office,
        ),
      };
    }
    return null;
  }

  function onKey(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null;
    if (
      target?.closest("input, textarea, select, [contenteditable]") != null ||
      event.metaKey ||
      event.ctrlKey ||
      event.altKey
    ) {
      return;
    }
    if (event.key === "Escape") {
      if (isNation && pick != null) {
        update({ pick: null });
      } else if (!isNation && county != null) {
        update({ county: null });
      } else if (up != null) {
        void goto(up, { reset: false });
      } else {
        return;
      }
      event.preventDefault();
    } else if (event.key === "+" || event.key === "=") {
      canvas?.zoomIn();
    } else if (event.key === "-" || event.key === "_") {
      canvas?.zoomOut();
    } else if (event.key === "0") {
      canvas?.reset();
    }
  }
</script>

<svelte:window onkeydown={onKey} />

{#snippet optionShares(summary: Summary, limit: number)}
  {const total = $derived(summary.total)}
  {const last = $derived(prior(summary.race))}
  <ul class="grid gap-1">
    {#each summary.race.options
      .map((option, index) => ({ option, index }))
      .filter((entry) => !entry.option.isWriteIn)
      .toSorted((a, b) => (summary.totals[b.index] ?? 0) - (summary.totals[a.index] ?? 0) || (last?.shares[b.index] ?? 0) - (last?.shares[a.index] ?? 0))
      .slice(0, limit) as entry (entry.option.id)}
      <li class="flex items-center gap-2">
        <span
          class="shrink-0 rounded-full block-2 inline-2"
          style:background-color={tone(entry.option)}
        ></span>
        <span
          class={cn("grow truncate", {
            "font-semibold": summary.leader?.id === entry.option.id,
          })}>{entry.option.label}</span
        >
        <span class="font-figures tabular-nums">
          {#if total > 0}
            {percent((summary.totals[entry.index] ?? 0) / total)}
          {:else if last != null}
            <span class="text-muted-foreground"
              >{percent(last.shares[entry.index] ?? 0)}</span
            >
          {/if}
        </span>
      </li>
    {/each}
  </ul>
{/snippet}

{#snippet tip(areaId: string)}
  {const nationTip = $derived(tipRace(areaId))}
  {#if nationTip != null}
    <p class="font-semibold">{nationTip.title}</p>
    {#if nationTip.lines.length === 0}
      <p class="text-xs text-muted-foreground">{m.night_nation_none()}</p>
    {:else if office === "house" && nationTip.lines.length > 1}
      {const groups = $derived(
        [
          ...Map.groupBy(
            nationTip.lines.flatMap((summary) => {
              const option =
                summary.winner ?? summary.projected ?? summary.leader;
              return option == null ? [] : [option];
            }),
            (option: LiveOption) => option.party?.short ?? option.label,
          ),
        ].toSorted((a, b) => b[1].length - a[1].length),
      )}
      <p class="text-xs text-muted-foreground">
        {m.night_nation_seats({ count: nationTip.lines.length })}
      </p>
      {#each groups as [name, list] (name)}
        <p class="flex items-center gap-2">
          <span
            class="rounded-full block-2 inline-2"
            style:background-color={tone(list[0])}
          ></span>
          <span class="grow">{name}</span>
          <span class="font-figures font-semibold tabular-nums"
            >{list.length}</span
          >
        </p>
      {/each}
    {:else}
      {#each nationTip.lines.slice(0, 2) as summary (summary.race.id)}
        {#if nationTip.lines.length > 1}
          <p class="text-xs text-muted-foreground">{summary.race.title}</p>
        {/if}
        {@render optionShares(summary, 3)}
        <p class="text-xs text-muted-foreground">{verdict(summary)}</p>
      {/each}
    {/if}
  {:else}
    {const entry = $derived(rowByArea.get(areaId))}
    {const name = $derived(counties.get(areaId)?.name ?? "")}
    <p class="font-semibold">{name}</p>
    {#if entry == null || race == null}
      <p class="text-xs text-muted-foreground">{m.livemap_not_here()}</p>
    {:else}
      {const row = $derived(entry.row)}
      <ul class="grid gap-1">
        {#each row.order.slice(0, 3) as index (index)}
          {const option = $derived(race.options[index])}
          {#if option != null && (row.total > 0 || (row.baseline?.shares[index] ?? 0) > 0)}
            <li class="flex items-center gap-2">
              <span
                class="shrink-0 rounded-full block-2 inline-2"
                style:background-color={tone(option)}
              ></span>
              <span
                class={cn("grow truncate", {
                  "font-semibold": row.leader === index,
                })}>{option.label}</span
              >
              <span class="font-figures tabular-nums">
                {#if row.total > 0}
                  {percent((row.votes[index] ?? 0) / row.total)}
                {:else}
                  <span class="text-muted-foreground"
                    >{percent(row.baseline?.shares[index] ?? 0)}</span
                  >
                {/if}
              </span>
            </li>
          {/if}
        {/each}
      </ul>
      <p class="text-xs text-muted-foreground">
        {#if entry.counted != null}
          {m.night_tip_counted({ share: percent(entry.counted, 0) })}
        {:else}
          {m.night_tip_waiting()}
        {/if}
      </p>
      {#if row.baseSigned != null}
        {const then = $derived(
          race.options[row.baseSigned >= 0 ? racePair[0] : racePair[1]],
        )}
        <p class="text-xs text-muted-foreground">
          {m.night_tip_baseline({
            margin: `${surname(then)} +${lead(Math.abs(row.baseSigned))}`,
          })}
        </p>
      {/if}
    {/if}
  {/if}
{/snippet}

{#snippet controls()}
  <div class="grid justify-items-start gap-2">
    {#if isNation}
      <Segmented
        options={officeOptions}
        value={office}
        label={m.night_nation_views()}
        size={wide.current ? "md" : "sm"}
        class="bg-card/95 shadow-menu backdrop-blur-sm"
        onValueChange={(value) => update({ office: value, pick: null })}
      />
    {:else}
      <Segmented
        options={raceOptions}
        value={raceGroup}
        label={m.livemap_races()}
        size={wide.current ? "md" : "sm"}
        class="bg-card/95 shadow-menu backdrop-blur-sm"
        onValueChange={(value: string) => pickRace(value)}
      />
      {#if raceGroup === "house" && seats.length > 1}
        <div
          class="flex gap-1 overflow-x-auto rounded-sm bg-card/95 p-1 shadow-menu ring-1 ring-border backdrop-blur-sm max-inline-[min(36rem,calc(100vw-4.5rem))]"
          role="group"
          aria-label={m.livemap_districts()}
        >
          {#if hasOverview}
            <button
              type="button"
              class={cn(
                "shrink-0 rounded-xs px-2.5 text-xs font-semibold block-7",
                {
                  "bg-foreground text-card": isOverview,
                  "hover:bg-secondary": !isOverview,
                },
              )}
              aria-pressed={isOverview}
              onclick={() => pickRace("house")}>{m.livemap_overview()}</button
            >
          {/if}
          {#each seats as summary (summary.race.id)}
            {const isOn = $derived(summary.race.id === race?.id)}
            {const color = $derived(seatShade(summary, "lead"))}
            <button
              type="button"
              class={cn(
                "flex shrink-0 items-center gap-1.5 rounded-xs px-2 font-figures text-xs font-semibold tabular-nums block-7",
                {
                  "bg-foreground text-card": isOn,
                  "hover:bg-secondary": !isOn,
                },
              )}
              aria-pressed={isOn}
              aria-label={summary.race.title}
              title={summary.race.title}
              onclick={() => pickRace(tail(summary.race))}
              ><span
                class="rounded-full ring-1 ring-card block-2 inline-2"
                style:background-color={color}
              ></span>{seatName(summary.race, stateName)
                .split("-")
                .at(-1)}</button
            >
          {/each}
        </div>
      {/if}
    {/if}
    {#if !wide.current}
      {@render modes("sm")}
    {/if}
  </div>
{/snippet}

{#snippet modes(size: "sm" | "md")}
  {#if isNation}
    <Segmented
      options={paintOptions}
      value={paint}
      label={m.night_color_label()}
      {size}
      class="bg-card/95 shadow-menu backdrop-blur-sm"
      onValueChange={(value) =>
        update({ color: value === "lead" ? null : value }, false)}
    />
  {:else}
    <Segmented
      options={lensOptions}
      value={lens}
      label={m.night_map_modes()}
      {size}
      class="bg-card/95 shadow-menu backdrop-blur-sm"
      onValueChange={(value) => update({ color: value }, false)}
    />
  {/if}
{/snippet}

{#snippet legend()}
  <Legend kind={legendKind} {sides} text={legendText} isLater={isLaterShown} />
{/snippet}

{#snippet summaryHead()}
  {#if isNation}
    {const view = $derived(found.chambers?.[office] ?? null)}
    <div class="flex items-center justify-between gap-3">
      <p class="text-[0.9375rem] font-[720]">{OFFICE_NAMES[office]()}</p>
      {#if view != null && found.pair != null}
        <p class="flex items-baseline gap-3 font-figures tabular-nums">
          {#each [0, 1] as const as side (side)}
            {const family = $derived(found.pair[side])}
            {const tally = $derived(
              side === 0 ? view.tallies.left : view.tallies.right,
            )}
            <span class="flex items-baseline gap-1">
              <span class="text-xs font-semibold" style:color={family.color}
                >{family.short}</span
              >
              <span class="text-lg font-[760]"
                >{tally.won +
                  tally.projected +
                  tally.ahead +
                  tally.holdover}</span
              >
            </span>
          {/each}
        </p>
      {/if}
    </div>
  {:else if current != null}
    <p class="truncate text-[0.9375rem] font-[720]">{current.race.title}</p>
    <p class="line-clamp-2 font-serif text-sm/snug text-pretty">
      {describe(current, closing(election, current.race), clock)}
    </p>
  {:else}
    <p class="text-[0.9375rem] font-[720]">{m.night_nation_house()}</p>
    <p class="text-sm text-muted-foreground">
      {m.night_nation_seats({ count: seats.length })}
    </p>
  {/if}
{/snippet}

{#snippet panel()}
  {#if isNation}
    <NationPanel
      {election}
      {found}
      {office}
      {paint}
      {control}
      states={stateName}
      picked={pick}
      {clock}
      {forecasts}
      onPick={(key) => update({ pick: key })}
      onOpen={open}
      onHover={(key: string | null) =>
        (hovered =
          key == null
            ? null
            : (areaOfKey[key] ?? nationCodes[key.split("-")[0] ?? ""] ?? null))}
    />
  {:else if county != null}
    <CountyCard
      name={county.name}
      areaId={county.areaId}
      {race}
      row={countyRow}
      {estimate}
      pair={racePair}
      statewide={race == null ? null : (prior(race)?.turnout ?? null)}
      {others}
      slug={election.slug}
      code={code ?? ""}
      stamp={election.newest ?? ""}
      href={toLive(`${election.slug}/place/${county.slug}`)}
      onRace={(next) => update({ race: next })}
      onClose={() => update({ county: null })}
    />
  {:else if isOverview}
    <section class="grid gap-1.5">
      <h2 class="text-xs font-semibold text-muted-foreground">
        {m.night_nation_house()}
      </h2>
      <ul class="grid">
        {#each seats.toSorted((a, b) => (urgency(a, closing(election, a.race))[0] ?? 0) - (urgency(b, closing(election, b.race))[0] ?? 0) || Number.parseInt(a.race.district ?? "0", 10) - Number.parseInt(b.race.district ?? "0", 10)) as summary (summary.race.id)}
          {const key = $derived(districtKey(summary.race) ?? "")}
          {const flip = $derived(
            flipped(found.seats.get(summary.race.id), found.pair, control),
          )}
          <li>
            <button
              type="button"
              class="-mx-2 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2.5 rounded-sm px-2 py-1.5 text-start inline-[calc(100%+1rem)] hover:bg-secondary focus-visible:bg-secondary"
              onclick={() => pickRace(tail(summary.race))}
              onpointerenter={() => (hovered = districtCodes[key] ?? null)}
              onpointerleave={() => (hovered = null)}
            >
              <span
                class="rounded-xs ring-1 ring-border/60 block-3.5 inline-3.5"
                style:background-color={seatShade(summary, "lead")}
              ></span>
              <span class="grid min-inline-0">
                <span
                  class="flex items-center gap-1.5 text-[0.8125rem] font-semibold"
                  >{seatName(summary.race, stateName)}
                  {#if flip != null}<span
                      class="text-[0.6875rem]"
                      style:color={flip.color}>{m.night_flip()}</span
                    >{/if}</span
                >
                <span class="truncate text-xs text-muted-foreground"
                  >{verdict(summary)}</span
                >
              </span>
              <span
                class="font-figures text-xs text-muted-foreground tabular-nums"
                >{summary.counted != null && summary.total > 0
                  ? percent(summary.counted, 0)
                  : ""}</span
              >
            </button>
          </li>
        {/each}
      </ul>
    </section>
  {:else if current != null}
    <div class="grid gap-6">
      <RaceCard
        summary={current}
        held={holding(control, current.race)}
        flip={flipped(found.seats.get(current.race.id), found.pair, control)}
        closes={closing(election, current.race)}
        {clock}
        {forecasts}
      />
      <CountyList
        {rows}
        options={current.race.options}
        pair={racePair}
        selected={null}
        onPick={(areaId: string) => {
          pickArea(areaId);
          canvas?.focus(areaId);
        }}
        onHover={(areaId: string | null) => (hovered = areaId)}
      />
    </div>
  {:else}
    <p class="text-sm text-muted-foreground">{m.night_nation_none()}</p>
  {/if}
{/snippet}

{#snippet footer()}
  <div
    class="grid gap-1 border-bs pbs-3 text-[0.6875rem]/4 text-muted-foreground"
  >
    {#if election.newest != null}
      <p>{m.night_updated({ time: stamp(election.newest) })}</p>
    {/if}
    {#if isOffline}
      <p>{m.night_note_offline({ time: stamp(election.newest) })}</p>
    {/if}
    {#if prefs.isBasemap}
      <p>{m.map_basemap_credit()}</p>
    {/if}
  </div>
{/snippet}

<div
  class="fixed inset-0 z-40 grid grid-rows-[auto_minmax(0,1fr)] bg-card text-foreground"
>
  <header
    class="relative z-30 flex items-center gap-x-3 border-be bg-card px-3 block-14 md:gap-x-4 md:px-4"
  >
    <a
      href={resolve("/")}
      class="shrink-0"
      aria-label={m.wordmark()}
      title={m.wordmark()}><Monogram size={26} /></a
    >
    <div class="grid flex-1 min-inline-0">
      <p class="truncate text-[0.9375rem]/5 font-[720] tracking-tight">
        {election.title}
      </p>
      <nav aria-label={m.atlas_trail()}>
        <ol
          class="flex items-center gap-1 text-[0.8125rem]/5 text-muted-foreground"
        >
          <li class="flex items-center gap-1 min-inline-0">
            {#if isNation}
              <span
                class={cn("truncate", {
                  "font-semibold text-foreground": pick == null,
                })}
                aria-current={pick == null ? "page" : undefined}
                >{m.livemap_nation()}</span
              >
            {:else}
              <a
                href={up}
                class="truncate underline decoration-border underline-offset-3 hover:text-foreground hover:decoration-foreground"
                >{m.livemap_nation()}</a
              >
            {/if}
          </li>
          {#if !isNation}
            <li class="flex items-center gap-1 min-inline-0">
              <span aria-hidden="true">/</span>
              {#if county == null}
                <span
                  class="truncate font-semibold text-foreground"
                  aria-current="page">{here}</span
                >
              {:else}
                <a
                  href={href(election.slug, {
                    race: raceTail,
                    color: params.get("color"),
                  })}
                  class="truncate underline decoration-border underline-offset-3 hover:text-foreground hover:decoration-foreground"
                  onclick={(event) => {
                    event.preventDefault();
                    update({ county: null });
                  }}>{here}</a
                >
                <span aria-hidden="true">/</span>
                <span
                  class="truncate font-semibold text-foreground"
                  aria-current="page">{county.name}</span
                >
              {/if}
            </li>
          {/if}
        </ol>
      </nav>
    </div>
    <span
      class={cn(
        "flex shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs font-semibold ring-1 block-7",
        {
          "text-foreground ring-border": status.isLive,
          "text-muted-foreground ring-border/70 max-sm:hidden": !status.isLive,
        },
      )}
      title={election.newest == null
        ? undefined
        : m.night_updated({ time: stamp(election.newest) })}
    >
      {#if status.isLive}
        <span class="relative grid place-items-center block-2 inline-2">
          <span
            class="absolute inset-0 animate-ping rounded-full bg-destructive/60 motion-reduce:animate-none"
          ></span>
          <span class="relative rounded-full bg-destructive block-2 inline-2"
          ></span>
        </span>
      {/if}
      {status.label}
    </span>
    <a
      href={toLive(election.slug)}
      class="flex shrink-0 items-center gap-1.5 rounded-sm px-2.5 text-[0.8125rem] font-semibold ring-1 ring-input block-8 hover:bg-secondary max-md:hidden"
      ><ArrowLeftIcon
        class="block-3.5 inline-3.5"
        aria-hidden="true"
      />{m.atlas_back()}</a
    >
    <a
      href={toLive(election.slug)}
      class="grid shrink-0 place-items-center rounded-sm block-9 inline-9 hover:bg-secondary"
      aria-label={m.atlas_close()}
      title={m.atlas_close()}><XIcon class="block-4.5 inline-4.5" /></a
    >
  </header>

  <div
    class={cn("grid min-block-0", {
      "grid-cols-[minmax(0,1fr)_min(25rem,36vw)]": wide.current,
    })}
  >
    <main class="relative min-block-0" bind:clientHeight={room}>
      {#if mapGeography == null}
        <div
          class="grid place-items-center p-6 text-center text-sm text-muted-foreground block-full"
        >
          {m.night_nation_map_missing()}
        </div>
      {:else}
        <Canvas
          bind:this={canvas}
          geography={mapGeography}
          {fills}
          {dots}
          {labels}
          {selected}
          bind:hovered
          {pulses}
          inset={{ top: controlsHeight + 12, bottom: wide.current ? 0 : sheet }}
          label={isNation
            ? m.night_nation_map({ view: OFFICE_NAMES[office]() })
            : (current?.race.title ?? m.night_nation_house())}
          onPick={pickArea}
          {tip}
        />
      {/if}

      <div
        class="pointer-events-none absolute inset-x-3 inset-bs-3 z-20 flex items-start justify-between gap-2"
      >
        <div class="pointer-events-auto" bind:clientHeight={controlsHeight}>
          {@render controls()}
        </div>
        {#if !wide.current}
          <div
            class="pointer-events-auto grid shrink-0 overflow-hidden rounded-sm bg-card/95 shadow-menu ring-1 ring-border"
          >
            <button
              type="button"
              class="grid place-items-center block-9 inline-9 hover:bg-secondary"
              aria-label={m.map_reset()}
              title={m.map_reset()}
              onclick={() => canvas?.reset()}
              ><ScanIcon class="block-4 inline-4" /></button
            >
            <button
              type="button"
              class={cn("grid place-items-center border-bs block-9 inline-9", {
                "bg-foreground text-card": prefs.isBasemap,
                "hover:bg-secondary": !prefs.isBasemap,
              })}
              aria-label={m.map_basemap()}
              title={m.map_basemap()}
              aria-pressed={prefs.isBasemap}
              onclick={() => (prefs.isBasemap = !prefs.isBasemap)}
              ><MapIcon class="block-4 inline-4" /></button
            >
          </div>
        {/if}
      </div>

      {#if loading != null || fresh.length > 0}
        <div
          class="pointer-events-none absolute inset-x-0 z-20 flex justify-center px-3"
          style:inset-block-start="{controlsHeight + 24}px"
          aria-live="polite"
        >
          <p
            class="flex items-center gap-2 rounded-full bg-foreground px-3.5 text-[0.8125rem] font-semibold text-background shadow-menu block-8"
          >
            {#if loading != null}
              <LoaderCircleIcon
                class="animate-spin block-3.5 inline-3.5 motion-reduce:animate-none"
                aria-hidden="true"
              />
              {m.map_opening({ place: loading })}
            {:else}
              <span
                class="rounded-full bg-destructive block-2 inline-2"
                aria-hidden="true"
              ></span>
              {m.livemap_new_votes({ places: fresh.join(", ") })}
            {/if}
          </p>
        </div>
      {/if}

      {#if wide.current}
        <div
          class="absolute inset-s-3 inset-be-3 z-20 grid gap-3 rounded-sm bg-card/95 p-3 shadow-menu ring-1 ring-border backdrop-blur-sm inline-[min(24rem,calc(100%-5rem))]"
        >
          {@render modes("sm")}
          {@render legend()}
        </div>
        <div class="absolute inset-e-3 inset-be-3 z-20 grid gap-1.5">
          <div
            class="grid overflow-hidden rounded-sm bg-card/95 shadow-menu ring-1 ring-border"
          >
            <button
              type="button"
              class="grid place-items-center block-9 inline-9 hover:bg-secondary"
              aria-label={m.map_zoom_in()}
              title={m.map_zoom_in()}
              onclick={() => canvas?.zoomIn()}
              ><PlusIcon class="block-4 inline-4" /></button
            >
            <button
              type="button"
              class="grid place-items-center border-bs block-9 inline-9 hover:bg-secondary"
              aria-label={m.map_zoom_out()}
              title={m.map_zoom_out()}
              onclick={() => canvas?.zoomOut()}
              ><MinusIcon class="block-4 inline-4" /></button
            >
            <button
              type="button"
              class="grid place-items-center border-bs block-9 inline-9 hover:bg-secondary"
              aria-label={m.map_reset()}
              title={m.map_reset()}
              onclick={() => canvas?.reset()}
              ><ScanIcon class="block-4 inline-4" /></button
            >
          </div>
          <button
            type="button"
            class={cn(
              "grid place-items-center rounded-sm shadow-menu ring-1 ring-border block-9 inline-9",
              {
                "bg-foreground text-card": prefs.isBasemap,
                "bg-card/95 hover:bg-secondary": !prefs.isBasemap,
              },
            )}
            aria-label={m.map_basemap()}
            title={m.map_basemap()}
            aria-pressed={prefs.isBasemap}
            onclick={() => (prefs.isBasemap = !prefs.isBasemap)}
            ><MapIcon class="block-4 inline-4" /></button
          >
        </div>
      {:else}
        <Drawer
          {room}
          bind:detent
          bind:height={sheet}
          label={m.atlas_results()}
        >
          {#snippet head()}
            <div class="grid gap-1 px-4 pbs-1 pbe-3">
              {@render summaryHead()}
            </div>
          {/snippet}
          <div class="grid gap-5 px-4 pbe-8">
            {@render panel()}
            {@render legend()}
            {@render footer()}
          </div>
        </Drawer>
      {/if}
    </main>

    {#if wide.current}
      <aside
        class="grid content-start gap-5 overflow-y-auto overscroll-contain border-s bg-card p-4"
        aria-label={m.atlas_results()}
      >
        {@render panel()}
        {@render footer()}
      </aside>
    {/if}
  </div>
</div>
