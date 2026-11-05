<script lang="ts">
  import type {
    LiveElection,
    LiveEstimate,
    LiveOption,
    LiveRace,
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
  import { onMount, untrack } from "svelte";
  import { MediaQuery } from "svelte/reactivity";
  import type { SegmentedOption } from "#lib/components/ui/segmented/index.js";
  import type { Entry } from "#lib/live/ballot.js";
  import type { Control } from "#lib/live/nation.js";
  import type { Geography, Pieces, UnitStanding } from "#lib/live/standing.js";
  import type { Summary } from "#lib/live/summary.js";
  import type { Ballot } from "#lib/server/live-ballot.js";
  import Drawer from "#lib/atlas/drawer.svelte";
  import Cycle from "#lib/components/live/cycle.svelte";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import SeatKey from "#lib/components/live/dashboard/seat-key.svelte";
  import Dial from "#lib/components/live/dial.svelte";
  import FlipKey from "#lib/components/live/flip-key.svelte";
  import RacePicker from "#lib/components/live/race-picker.svelte";
  import Search from "#lib/components/live/search.svelte";
  import Also from "#lib/components/live/state/also.svelte";
  import Bar from "#lib/components/live/state/bar.svelte";
  import Card from "#lib/components/live/state/card.svelte";
  import Withheld from "#lib/components/live/withheld.svelte";
  import Monogram from "#lib/components/monogram.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { Skeleton } from "#lib/components/ui/skeleton/index.js";
  import { Switch } from "#lib/components/ui/switch/index.js";
  import { count, percent } from "#lib/format.js";
  import {
    entries as entriesOf,
    entryHatch,
    seating,
    turn,
  } from "#lib/live/ballot.js";
  import { swing } from "#lib/live/flips.js";
  import {
    isDistricted,
    lead,
    placeName,
    placeScheme,
    placeSlug,
    stamp,
    surname,
    tone,
  } from "#lib/live/format.js";
  import { history } from "#lib/live/history.js";
  import { lineup, provideReach, reach } from "#lib/live/lineup.js";
  import { liveLinks } from "#lib/live/links.js";
  import { seatsOf } from "#lib/live/nation.js";
  import { prefs } from "#lib/live/prefs.svelte.js";
  import {
    pair as pairOf,
    remainder,
    standing,
    sum,
    whole,
  } from "#lib/live/standing.js";
  import { closing, describe, summarize, verdict } from "#lib/live/summary.js";
  import { reach as arrowReach } from "#lib/map/engine.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { bands, MARGIN, marginShade, safety, SWING } from "#lib/shade.js";
  import { cn } from "#lib/utils.js";
  import type { Dot, Fill, Lens, Office, Paint, Swing } from "./model.js";
  import Canvas from "./canvas.svelte";
  import CountyCard from "./county-card.svelte";
  import CountyList from "./county-list.svelte";
  import Insets from "./insets.svelte";
  import Legend from "./legend.svelte";
  import {
    BLANK,
    board,
    districtKey,
    flipped,
    GROUND,
    holding,
    INSETS,
    isOutlying,
    isRightward,
    LENSES,
    MUTED,
    OFFICES,
    PAINTS,
    prior,
    seatHatch,
    seatName,
    shade,
    stateShade,
    swingOf,
    tail,
    TERRITORIES,
    unitFill,
    unitHatch,
    urgency,
    WAITING,
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
    legislature = null,
    around = null,
    nationPath,
    names,
    frame,
    boxes,
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
    legislature?: {
      chambers: Array<Ballot["chambers"][number] & { map: Geography }>;
      contests: Ballot["contests"];
      parties: Ballot["parties"];
      families: Ballot["families"];
    } | null;
    around?: {
      tiles: string;
      places: Geography["places"];
      codes: Record<string, string>;
    } | null;
    nationPath: string;
    names: Record<string, string>;
    frame: Geography["frame"] | null;
    boxes: Record<string, Geography["frame"]>;
    clock: number;
    isOffline: boolean;
    forecasts: boolean;
  } = $props();

  const VIEW = { at: null, isFinal: true };
  const wide = new MediaQuery("min-width: 48rem", false);

  let isMounted = $state(false);
  onMount(() => {
    isMounted = true;
  });
  const toLive = liveLinks();

  let canvas = $state<{
    focus: (areaId: string) => void;
    fly: (box: Geography["frame"], isHome: boolean) => void;
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
  let legendHeight = $state(0);
  let pulses = $state<Array<{ key: string; areaId: string; color: string }>>(
    [],
  );
  let fresh = $state<Array<string>>([]);

  const isNation = $derived(nation != null);
  let isNationFramed = $derived(isNation);
  const date = $derived(election.slug.split("/").at(-1) ?? "");
  const code = $derived(
    isNation ? null : (election.slug.split("/")[1] ?? null),
  );
  const found = $derived(board(election, estimates, control));
  const chambers = $derived(
    legislature == null || code == null
      ? []
      : legislature.chambers
          .toSorted(
            (a, b) => Number(a.tier === "lower") - Number(b.tier === "lower"),
          )
          .flatMap((entry) => {
            const list = entriesOf({
              contests: legislature.contests.flatMap((row) =>
                row.chamber === entry.id ? [row.contest] : [],
              ),
              election,
              estimates,
              parties: legislature.parties,
              families: legislature.families,
              state: code,
              tier: entry.tier,
            }).toSorted((a, b) =>
              a.contest.title.localeCompare(b.contest.title, undefined, {
                numeric: true,
              }),
            );
            let up = 0;
            for (const item of list) {
              up += item.contest.seats;
            }
            return list.length === 0
              ? []
              : [
                  {
                    ...entry,
                    entries: list,
                    up,
                    areas: Map.groupBy(
                      list.filter((item) => item.contest.area != null),
                      (item) => item.contest.area ?? "",
                    ),
                    seats: seating(list, entry.holdovers, legislature.parties),
                  },
                ];
          }),
  );
  let nationwide = $state<{
    races: Array<LiveRace>;
    estimates: Record<string, LiveEstimate>;
  } | null>(null);
  let isFetchingNation = false;
  function loadNation() {
    if (isNation || nationwide != null || isFetchingNation) {
      return;
    }
    isFetchingNation = true;
    orpc.live.nation
      .call({ path: nationPath })
      .then((result) => {
        nationwide = result;
      })
      .catch(() => {
        isFetchingNation = false;
      });
  }
  const everyRace = $derived.by(() => {
    if (isNation || nationwide == null) {
      return found.summaries;
    }
    const { estimates: all } = nationwide;
    return nationwide.races.map((entry) =>
      summarize(entry, all[entry.id] ?? null),
    );
  });
  const everyState = $derived(
    [...new Set(everyRace.map((summary) => summary.race.state))].toSorted(
      (a, b) => stateName(a).localeCompare(stateName(b)),
    ),
  );
  const chamberOf = $derived(
    new Map(
      chambers.flatMap((entry) =>
        entry.entries.map((item) => [item.contest.slug, entry.tier] as const),
      ),
    ),
  );
  const parties = $derived(
    reach([
      ...election.races,
      ...chambers.flatMap((entry) =>
        entry.entries.map((item) => item.summary.race),
      ),
    ]),
  );
  provideReach(() => parties);
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
  const isSpread = $derived(isNation && params.get("view") === "counties");

  let spread = $state.raw<{
    office: Office;
    value: Awaited<ReturnType<typeof orpc.live.counties.call>>;
  } | null>(null);
  let isSpreading = $state(false);

  $effect(() => {
    if (!isSpread) {
      return;
    }
    const wanted = office;
    void election.newest;
    let isCurrent = true;
    isSpreading = true;
    orpc.live.counties.call({ path: nationPath, office: wanted }).then(
      (value) => {
        if (isCurrent) {
          spread = { office: wanted, value };
          isSpreading = false;
        }
      },
      () => {
        if (isCurrent) {
          isSpreading = false;
        }
      },
    );
    return () => {
      isCurrent = false;
    };
  });

  const spreadData = $derived(
    isSpread && spread?.office === office ? spread.value : null,
  );

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
    if (chambers.some((entry) => entry.tier === wanted)) {
      return wanted ?? "";
    }
    if (wanted === "house") {
      if (hasOverview) {
        return "house";
      }
      const seat = seats[0];
      if (seat != null) {
        return tail(seat.race);
      }
    }
    if (
      wanted != null &&
      found.summaries.some((summary) => tail(summary.race) === wanted)
    ) {
      return wanted;
    }
    const matched = statewide.find((summary) => summary.race.office === wanted);
    if (matched != null) {
      return tail(matched.race);
    }
    const first = statewide[0] ?? seats[0];
    if (first == null) {
      return hasOverview ? "house" : (chambers[0]?.tier ?? "");
    }
    return statewide.length === 0 && hasOverview ? "house" : tail(first.race);
  });
  const isOverview = $derived(!isNation && raceTail === "house");
  const chamber = $derived(
    isNation
      ? null
      : (chambers.find((entry) => entry.tier === raceTail) ?? null),
  );
  const pickedSeat = $derived.by(() => {
    const wanted = params.get("seat");
    return wanted == null
      ? null
      : (chamber?.entries.find((entry) => tail(entry.summary.race) === wanted)
          ?.contest.area ?? null);
  });
  const pickedEntries = $derived(
    pickedSeat == null ? [] : (chamber?.areas.get(pickedSeat) ?? []),
  );
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
  const forecast = $derived.by(() => {
    if (
      race == null ||
      current == null ||
      estimate == null ||
      estimate.status === "unavailable" ||
      race.certified != null ||
      current.settled != null ||
      current.total === 0 ||
      (race.isMeasure === true &&
        race.options.filter((option) => !option.isWriteIn).length > 2)
    ) {
      return null;
    }
    const [a, b] = racePair;
    return {
      history: history(race, null),
      counted:
        ((current.totals[a] ?? 0) - (current.totals[b] ?? 0)) / current.total,
    };
  });
  const hasVotes = $derived(
    isSpread
      ? (spreadData?.races.some((entry) => entry.counts.length > 0) ?? false)
      : isNation
        ? found.summaries.some(
            (summary) => summary.race.office === office && summary.total > 0,
          )
        : chamber != null
          ? chamber.entries.some((entry) => entry.summary.total > 0)
          : (current?.total ?? 0) > 0,
  );
  const lens = $derived.by((): Lens => {
    const wanted = params.get("color");
    const match = LENSES.find((entry) => entry === wanted);
    if (isOverview || chamber != null) {
      return match === "counted" ? "counted" : "lead";
    }
    return match != null && (hasVotes || match === "last") ? match : "lead";
  });

  const counties = $derived.by(() => {
    const byArea = new Map<
      string,
      { areaId: string; name: string; slug: string }
    >();
    for (const summary of found.summaries) {
      for (const unit of summary.race.units) {
        if (
          unit.scheme === placeScheme(summary.race.state) &&
          unit.areaId != null
        ) {
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
    isNation || chamber != null
      ? null
      : (bySlug.get(params.get("county") ?? "") ?? null),
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
      expected: number;
      color: string;
      stripe: string | null;
    }> = [];
    for (const unit of race.units) {
      const row = raceStanding.units.get(unit.id);
      if (
        row == null ||
        unit.areaId == null ||
        unit.scheme !== placeScheme(race.state)
      ) {
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
        expected: rest.expected,
        color: unitFill(row, lens, race.options, racePair, rest.expected),
        stripe: unitStripe(row, race.options),
      });
    }
    return out;
  });
  const rowByArea = $derived(
    new Map(rows.map((entry) => [entry.areaId, entry])),
  );
  const entire = $derived.by(() => {
    if (race == null || raceStanding == null || rows.length > 0) {
      return null;
    }
    const unit = race.units.find((unit) => unit.scheme === "us.state");
    const row = unit == null ? null : raceStanding.units.get(unit.id);
    if (unit?.areaId == null || row == null) {
      return null;
    }
    const rest = remainder(row, estimate, race.options, racePair);
    return {
      areaId: unit.areaId,
      color: unitFill(row, lens, race.options, racePair, rest.expected),
      stripe: unitStripe(row, race.options),
    };
  });

  const spreadRows = $derived.by(() => {
    const out = new Map<
      string,
      {
        areaId: string;
        name: string;
        state: string;
        race: LiveRace;
        row: UnitStanding;
        pair: [number, number];
        counted: number | null;
        color: string;
        stripe: string | null;
      }
    >();
    for (const entry of spreadData?.races ?? []) {
      const held = standing(entry, VIEW);
      const two = pairOf(entry);
      for (const unit of entry.units) {
        const row = held.units.get(unit.id);
        if (unit.areaId == null || row == null || out.has(unit.areaId)) {
          continue;
        }
        const rest = remainder(row, null, entry.options, two);
        out.set(unit.areaId, {
          areaId: unit.areaId,
          name: placeName(unit.name),
          state: entry.state,
          race: entry,
          row,
          pair: two,
          counted:
            row.total > 0
              ? Math.min(1, row.total / Math.max(1, rest.expected))
              : null,
          color: unitFill(row, lens, entry.options, two, rest.expected),
          stripe: unitStripe(row, entry.options),
        });
      }
    }
    return out;
  });

  const nationCodes = $derived(nation?.codes ?? {});
  const nearCodes = $derived(
    new Map(Object.entries(around?.codes ?? {}).map(([key, id]) => [id, key])),
  );
  const districtCodes = $derived(districts?.codes ?? {});
  const aim = $derived(
    isNation || race == null
      ? null
      : (boxes[districtCodes[districtKey(race) ?? ""] ?? ""] ?? null),
  );

  const seatArea = $derived(
    isNation || race == null || districts == null
      ? null
      : (districtCodes[districtKey(race) ?? ""] ?? null),
  );
  let cut = $state.raw<{ areaId: string; found: Pieces } | null>(null);
  const seat = $derived(
    cut != null && cut.areaId === seatArea ? cut.found : null,
  );

  const nearSeats = $derived(
    race?.office !== "house" || seatArea == null || isOverview
      ? null
      : new Map(
          Object.entries(districtCodes).flatMap(([key, id]) =>
            id === seatArea ? [] : [[id, key] as const],
          ),
        ),
  );
  const isAlone = $derived(isOutlying(code));
  const nearby = $derived(
    isAlone
      ? null
      : nearSeats != null && districts != null
        ? {
            tiles: districts.map.tiles,
            places: districts.map.places.flatMap((place) => {
              const [state = "", number = ""] =
                nearSeats.get(place.areaId)?.split("-") ?? [];
              return state === ""
                ? []
                : [
                    {
                      ...place,
                      name: `${state.toUpperCase()}-${number === "0" ? "AL" : number}`,
                    },
                  ];
            }),
          }
        : around == null
          ? null
          : {
              tiles: around.tiles,
              places: around.places.map((place) => ({
                ...place,
                name: stateName(nearCodes.get(place.areaId) ?? ""),
              })),
            },
  );
  const hold = $derived.by(() => {
    const k = districts?.map.places.find(
      (place) => place.areaId === seatArea,
    )?.k;
    return seat != null || districts == null || k == null
      ? null
      : { tiles: districts.map.tiles, k, color: shared ?? BLANK };
  });

  $effect(() => {
    const areaId = seatArea;
    if (areaId == null || untrack(() => cut?.areaId) === areaId) {
      return;
    }
    let isGone = false;
    const load = async (tries: number): Promise<void> => {
      try {
        const response = await fetch(`/live/pieces/${election.date}/${areaId}`);
        if (response.ok) {
          const found = (await response.json()) as Pieces;
          if (!isGone) {
            cut = { areaId, found };
          }
          return;
        }
        if (response.status === 404) {
          return;
        }
      } catch {}
      if (tries > 1 && !isGone) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        return load(tries - 1);
      }
    };
    void load(4);
    return () => {
      isGone = true;
    };
  });

  function flyTo(state: string, district: string | null = null) {
    isNationFramed = false;
    if (isAlone || isOutlying(state)) {
      return;
    }
    const box = district == null ? null : boxes[districtCodes[district] ?? ""];
    if (box != null) {
      canvas?.fly(box, false);
      return;
    }
    const home = boxes[nationCodes[state] ?? around?.codes[state] ?? ""];
    if (home != null) {
      canvas?.fly(home, true);
    }
  }
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

  const isSpreadShown = $derived(spreadData?.map != null);
  const stateKeys = $derived(
    new Map(Object.entries(nationCodes).map(([key, areaId]) => [areaId, key])),
  );

  function stateOf(areaId: string) {
    if (isSpreadShown) {
      return spreadRows.get(areaId)?.state ?? null;
    }
    return keyOfArea.get(areaId)?.split("-")[0] ?? null;
  }

  const baseGeography = $derived(
    isNation
      ? isSpreadShown
        ? (spreadData?.map ?? null)
        : isHouseMap
          ? (districts?.map ?? null)
          : (nation?.map ?? null)
      : isOverview
        ? (districts?.map ?? null)
        : (chamber?.map ?? geography),
  );
  const insetSets = $derived(
    isNation
      ? [
          ...(isSpreadShown && spreadData?.districts != null
            ? [spreadData.districts]
            : []),
          ...(baseGeography == null ? [] : [baseGeography]),
        ].map((set) => ({
          tiles: set.tiles,
          places: set.places.filter((place) =>
            INSETS.has(stateOf(place.areaId) ?? ""),
          ),
        }))
      : [],
  );
  const mapGeography = $derived.by((): Geography | null => {
    if (!isNation || baseGeography == null) {
      return baseGeography;
    }
    const outside = new Set([...INSETS, ...TERRITORIES]);
    return {
      ...baseGeography,
      places: baseGeography.places.filter(
        (place) => !outside.has(stateOf(place.areaId) ?? ""),
      ),
      frame: frame ?? baseGeography.frame,
    };
  });

  function seatShade(summary: Summary, wanted: Paint) {
    return shade(
      summary,
      found.seats.get(summary.race.id),
      wanted,
      found.pair,
      control,
    );
  }

  function unitStripe(row: UnitStanding, options: Array<LiveOption>) {
    return lens === "lead" ? (unitHatch(row, options)?.stripe ?? null) : null;
  }

  function seatMark(summary: Summary, wanted: Paint) {
    return seatHatch(
      summary,
      found.seats.get(summary.race.id),
      wanted,
      found.pair,
      control,
    );
  }

  function seatFill(summary: Summary, wanted: Paint): Fill {
    return {
      color: seatShade(summary, wanted),
      isLater: summary.later != null,
      stripe: seatMark(summary, wanted)?.stripe ?? null,
    };
  }

  function seatLabel(entry: Entry) {
    const { title } = entry.contest;
    const cut = title.lastIndexOf(", ");
    return cut === -1 ? title : title.slice(cut + 2);
  }

  function chamberMark(entry: Entry, wanted: Paint) {
    return wanted === "counted" ? null : entryHatch(entry);
  }

  function chamberShade(entry: Entry, wanted: Paint) {
    const { summary } = entry;
    const mark = chamberMark(entry, wanted);
    if (mark != null) {
      return mark.fill;
    }
    return wanted === "lead" &&
      entry.held != null &&
      summary.later == null &&
      summary.settled == null &&
      summary.total === 0
      ? marginShade(entry.held.color, safety(0.22))
      : shade(summary, undefined, wanted, null, null);
  }

  function chamberFill(list: Array<Entry> | undefined, wanted: Paint) {
    const first = list?.find((entry) => !entry.contest.isSpecial) ?? list?.[0];
    return first == null
      ? { color: MUTED }
      : {
          color: chamberShade(first, wanted),
          isLater: first.summary.later != null,
          stripe: chamberMark(first, wanted)?.stripe ?? null,
        };
  }

  const district = $derived(
    seatArea == null || raceStanding == null ? null : whole(raceStanding),
  );
  const sharedStripe = $derived(
    district == null || race == null
      ? null
      : unitStripe(district, race.options),
  );
  const shared = $derived(
    district == null || race == null
      ? null
      : lens === "votes"
        ? BLANK
        : unitFill(
            district,
            lens,
            race.options,
            racePair,
            sum(rows.map((entry) => entry.expected)),
          ),
  );

  const painted = $derived.by(() => {
    const out = new Map<string, Fill>();
    if (isSpreadShown) {
      for (const [areaId, entry] of spreadRows) {
        out.set(areaId, {
          color: lens === "votes" ? BLANK : entry.color,
          stripe: entry.stripe,
        });
      }
      return out;
    }
    if (isNation && !isHouseMap) {
      for (const [key, areaId] of Object.entries(nationCodes)) {
        const races = (found.byState.get(key) ?? []).filter(
          (summary) => summary.race.office === office,
        );
        out.set(areaId, stateShade(races, office, paint, found, control));
      }
      return out;
    }
    if (chamber != null) {
      const wanted: Paint = lens === "counted" ? "counted" : "lead";
      for (const place of chamber.map.places) {
        out.set(
          place.areaId,
          chamberFill(chamber.areas.get(place.areaId), wanted),
        );
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
          summary == null ? { color: MUTED } : seatFill(summary, wanted),
        );
      }
      return out;
    }
    const isLater = current?.later != null;
    for (const place of geography?.places ?? []) {
      const entry =
        rowByArea.get(place.areaId) ??
        (place.areaId === entire?.areaId ? entire : null);
      out.set(
        place.areaId,
        entry == null
          ? { color: MUTED }
          : {
              color: lens === "votes" ? BLANK : (shared ?? entry.color),
              isLater,
              stripe: shared == null ? entry.stripe : sharedStripe,
            },
      );
    }
    return out;
  });

  const isArrows = $derived(params.get("arrows") !== "off");
  const swings = $derived.by((): Array<Swing> => {
    if (lens !== "shift" || !isArrows) {
      return [];
    }
    if (isSpreadShown) {
      return [...spreadRows.values()].flatMap(
        (entry) =>
          swingOf(entry.areaId, entry.row, entry.race.options, entry.pair) ??
          [],
      );
    }
    if (isNation || isOverview || chamber != null || race == null) {
      return [];
    }
    return rows.flatMap(
      (entry) => swingOf(entry.areaId, entry.row, race.options, racePair) ?? [],
    );
  });
  const swingReach = $derived(arrowReach(swings.map((entry) => entry.swing)));
  const isSwingable = $derived(
    lens === "shift" &&
      (isSpreadShown ||
        (!isNation && !isOverview && chamber == null && race != null)),
  );
  const fills = $derived.by(() => {
    if (swings.length === 0) {
      return painted;
    }
    const arrowed = new Set(swings.map((entry) => entry.areaId));
    const out = new Map<string, Fill>();
    for (const [areaId, fill] of painted) {
      out.set(areaId, arrowed.has(areaId) ? { ...fill, color: GROUND } : fill);
    }
    return out;
  });

  const dots = $derived.by((): Array<Dot> => {
    if (isSpreadShown) {
      return lens === "votes"
        ? [...spreadRows.values()].flatMap((entry) =>
            entry.row.total > 0 && entry.row.leader != null
              ? [
                  {
                    areaId: entry.areaId,
                    value: entry.row.total,
                    color: tone(entry.race.options[entry.row.leader]),
                  },
                ]
              : [],
          )
        : [];
    }
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
      return isHouseMap || isSpreadShown
        ? []
        : Object.entries(nationCodes).map(([key, areaId], index) => ({
            areaId,
            text: stateName(key),
            rank: index,
          }));
    }
    if (chamber != null) {
      return chamber.map.places.flatMap((place, index) => {
        const first = chamber.areas.get(place.areaId)?.[0];
        return first == null
          ? []
          : [
              {
                areaId: place.areaId,
                text: seatLabel(first).replace(/^District /u, ""),
                rank: index,
              },
            ];
      });
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
      : chamber != null
        ? pickedSeat
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

  function open(target: string, picked: string | null = pick) {
    const district =
      office === "house" && picked?.startsWith(`${target}-`) === true
        ? found.districts.get(picked)
        : null;
    flyTo(target, district == null ? null : picked);
    void goto(
      href(`${election.slug.split("/")[0] ?? "us"}/${target}/${date}`, {
        race:
          district == null
            ? office === "house"
              ? "house"
              : office
            : tail(district.race),
        color:
          paint === "counted" || (isSpread && lens === "counted")
            ? "counted"
            : null,
      }),
      { reset: false },
    );
  }

  function openCounty(areaId: string) {
    const entry = spreadRows.get(areaId);
    if (entry == null) {
      return;
    }
    flyTo(entry.state);
    const home =
      office === "house"
        ? (spreadData?.homes[areaId] ?? null)
        : tail(entry.race);
    void goto(
      href(`${election.slug.split("/")[0] ?? "us"}/${entry.state}/${date}`, {
        race: home ?? "house",
        county:
          home == null
            ? null
            : placeSlug(
                entry.race.units.find((unit) => unit.areaId === areaId)?.name ??
                  "",
              ),
        color: params.get("color"),
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
        }),
  );

  let awayAt = 0;
  let awayTimer: ReturnType<typeof setTimeout> | undefined;

  function rise() {
    if (isNation) {
      update({ pick: null });
    } else if (pickedSeat != null) {
      update({ seat: null });
    } else if (county != null) {
      update({ county: null });
    } else if (up != null) {
      isNationFramed = true;
      if (frame != null && !isAlone) {
        canvas?.fly(frame, true);
      }
      void goto(up, { reset: false });
    }
  }

  function away() {
    clearTimeout(awayTimer);
    const now = performance.now();
    if (now - awayAt < 320) {
      awayAt = 0;
      return;
    }
    awayAt = now;
    awayTimer = setTimeout(rise, 320);
  }

  function visit(target: string) {
    flyTo(target);
    void goto(
      href(`${election.slug.split("/")[0] ?? "us"}/${target}/${date}`, {
        race:
          chamber != null
            ? chamber.tier
            : race?.office === "house" || isOverview
              ? "house"
              : (race?.office ?? null),
        color: params.get("color"),
      }),
      { reset: false },
    );
  }

  $effect(() => () => clearTimeout(awayTimer));

  function pickArea(areaId: string | null) {
    if (areaId == null) {
      away();
      return;
    }
    clearTimeout(awayTimer);
    const seatKey = nearSeats?.get(areaId);
    if (seatKey != null) {
      const [state = "", number = ""] = seatKey.split("-");
      if (state === code) {
        update({ race: `house-${number}`, county: null });
        return;
      }
      flyTo(state, seatKey);
      void goto(
        href(`${election.slug.split("/")[0] ?? "us"}/${state}/${date}`, {
          race: `house-${number}`,
          color: params.get("color"),
        }),
        { reset: false },
      );
      return;
    }
    const neighbor = nearCodes.get(areaId);
    if (neighbor != null) {
      visit(neighbor);
      return;
    }
    if (isNation) {
      if (isSpreadShown) {
        openCounty(areaId);
        return;
      }
      const key = keyOfArea.get(areaId) ?? stateKeys.get(areaId) ?? null;
      if (key == null) {
        update({ pick: null });
        return;
      }
      open(key.split("-")[0] ?? key, key);
      return;
    }
    if (chamber != null) {
      const first = chamber.areas.get(areaId)?.[0];
      update({ seat: first == null ? null : tail(first.summary.race) });
      if (first != null && !wide.current && detent === "peek") {
        detent = "half";
      }
      return;
    }
    if (isOverview) {
      const key = keyOfArea.get(areaId);
      const summary = key == null ? null : found.districts.get(key);
      if (summary != null) {
        update({ race: tail(summary.race), county: null });
      }
      return;
    }
    const entry = counties.get(areaId);
    update({ county: entry?.slug ?? null });
    if (entry != null && !wide.current && detent === "peek") {
      detent = "half";
    }
  }

  function pickRace(next: string) {
    update({
      race: next,
      seat: null,
      county: chambers.some((entry) => entry.tier === next)
        ? null
        : (county?.slug ?? null),
    });
  }

  function searchLink(slug: string) {
    const tier = chamberOf.get(slug);
    if (
      tier == null &&
      !everyRace.some((summary) => summary.race.slug === slug)
    ) {
      return toLive(slug);
    }
    const parts = slug.split("/");
    const end = parts.pop() ?? "";
    return href(
      parts.join("/"),
      tier == null ? { race: end } : { race: tier, seat: end },
    );
  }

  function pickSummary(summary: Summary) {
    const tier = chamberOf.get(summary.race.slug);
    if (tier == null) {
      pickRace(tail(summary.race));
    } else {
      update({ race: tier, seat: tail(summary.race), county: null });
    }
  }

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
        ([value]) =>
          (!isOverview && chamber == null) ||
          value === "lead" ||
          value === "counted",
      )
      .map(([value, label]) => ({
        value,
        label: label(),
        disabled:
          !hasVotes &&
          !isOverview &&
          chamber == null &&
          value !== "last" &&
          value !== "lead",
      })),
  );
  const viewOptions = $derived<Array<SegmentedOption<string>>>([
    {
      value: "states",
      label:
        office === "house"
          ? m.livemap_view_districts()
          : m.livemap_view_states(),
    },
    { value: "counties", label: m.livemap_view_counties() },
  ]);
  const territories = $derived(
    isNation
      ? TERRITORIES.flatMap((key) => {
          const areaId = nationCodes[key];
          if (areaId == null) {
            return [];
          }
          const wanted: Paint = isSpread
            ? lens === "counted"
              ? "counted"
              : "lead"
            : paint;
          if (office === "house") {
            const seat = [...found.districts].find(([seat]) =>
              seat.startsWith(`${key}-`),
            );
            return seat == null
              ? []
              : [
                  {
                    key,
                    areaId,
                    seat: seat[0],
                    color: seatShade(seat[1], wanted),
                  },
                ];
          }
          const races = (found.byState.get(key) ?? []).filter(
            (summary) => summary.race.office === office,
          );
          return races.length === 0
            ? []
            : [
                {
                  key,
                  areaId,
                  seat: key,
                  color: stateShade(races, office, wanted, found, control)
                    .color,
                },
              ];
        })
      : [],
  );
  let lit = $state<string | null>(null);
  const insetStature = $derived(
    wide.current ? Math.min(104, Math.max(56, room * 0.13)) : 58,
  );
  const sides = $derived.by(() => {
    if (isNation || isOverview || chamber != null) {
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
  const legendKind = $derived.by(
    (): "ramp" | "counted" | "votes" | "waiting" => {
      const value = isNation && !isSpread ? paint : lens;
      if (value === "lead" && !hasVotes) {
        return "waiting";
      }
      return value === "counted"
        ? "counted"
        : value === "votes"
          ? "votes"
          : "ramp";
    },
  );
  const arrowSides = $derived(
    race != null && !isSpreadShown && isRightward(race.options, racePair)
      ? sides.toReversed()
      : sides,
  );
  const legendBins = $derived(
    (isNation && !isSpread) || legendKind !== "ramp"
      ? null
      : lens === "shift"
        ? bands(SWING)
        : lens === "lead" || lens === "last"
          ? bands(MARGIN)
          : null,
  );
  const legendText = $derived.by(() => {
    if (legendKind === "waiting") {
      return m.livemap_legend_waiting_hint();
    }
    if (isNation && !isSpread) {
      return {
        lead: m.night_nation_legend,
        chance: m.night_nation_legend_chance,
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
  const changing = $derived(
    isNation && !isSpread && paint !== "counted"
      ? swing(
          [...found.seats.values()]
            .filter((entry) => entry.summary.race.office === office)
            .map((entry) => entry.shift),
        )
      : null,
  );
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
    if (isSpread && isSpreading && spreadData == null) {
      return m.livemap_counties_loading();
    }
    const to = navigating.to;
    if (
      to?.route.id !== "/live/map/[...path]" ||
      to.url.pathname === page.url.pathname
    ) {
      return null;
    }
    const segments = to.params?.path?.split("/") ?? [];
    return m.map_opening({
      place:
        segments.length === 2
          ? m.livemap_nation()
          : stateName(segments[1] ?? ""),
    });
  });

  function tipRace(areaId: string) {
    if (isSpreadShown) {
      return null;
    }
    if (isNation || isOverview) {
      const key = keyOfArea.get(areaId) ?? stateKeys.get(areaId);
      if (key == null) {
        return null;
      }
      if (isHouseMap && !key.includes("-")) {
        return {
          title: stateName(key),
          lines: [...found.districts].flatMap(([seat, summary]) =>
            seat.startsWith(`${key}-`) ? [summary] : [],
          ),
        };
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
      if (isNation ? pick == null : county == null && up == null) {
        return;
      }
      rise();
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

{#snippet tipLines(
  race: LiveRace,
  totals: Array<number>,
  before: Array<number> | null,
  leader: string | null,
)}
  {const split = $derived(lineup(race, totals, parties, 3))}
  {const total = $derived(sum(totals))}
  <ul class="grid gap-1">
    {#each split.main as line (line.option.id)}
      {const then = $derived(before?.[race.options.indexOf(line.option)] ?? 0)}
      <li class="flex items-center gap-2">
        <span
          class="shrink-0 rounded-full block-2 inline-2"
          style:background-color={tone(line.option)}
        ></span>
        <span
          class={cn("grow truncate", {
            "font-semibold": leader === line.option.id,
          })}>{line.option.label}</span
        >
        <span class="font-figures tabular-nums">
          {#if total > 0}
            {percent(line.share ?? 0)}
          {:else if then > 0}
            <span class="text-muted-foreground">{percent(then)}</span>
          {/if}
        </span>
      </li>
    {/each}
  </ul>
  <Also rest={split.rest} {total} />
{/snippet}

{#snippet countyTip(
  name: string,
  shown: LiveRace | null,
  row: UnitStanding | null,
  counted: number | null,
  two: [number, number],
)}
  <p class="font-semibold">{name}</p>
  {#if row == null || shown == null}
    <p class="text-xs text-muted-foreground">{m.livemap_not_here()}</p>
  {:else}
    {@render tipLines(
      shown,
      row.votes,
      row.baseline?.shares ?? null,
      row.leader == null ? null : (shown.options[row.leader]?.id ?? null),
    )}
    <p class="text-xs text-muted-foreground">
      {#if counted != null}
        {m.night_tip_counted({ share: percent(counted, 0) })}
      {:else}
        {m.night_tip_waiting()}
      {/if}
    </p>
    {#if row.baseSigned != null}
      {const then = $derived(
        shown.options[row.baseSigned >= 0 ? two[0] : two[1]],
      )}
      <p class="text-xs text-muted-foreground">
        {m.night_tip_baseline({
          margin: `${surname(then)} +${lead(Math.abs(row.baseSigned))}`,
        })}
      </p>
    {/if}
  {/if}
{/snippet}

{#snippet tip(areaId: string)}
  {const nationTip = $derived(tipRace(areaId))}
  {const neighbor = $derived(nearCodes.get(areaId) ?? null)}
  {const nearSeat = $derived(
    nearby?.places.find((place) => place.areaId === areaId) ?? null,
  )}
  {#if nearSeats?.has(areaId) === true && nearSeat != null}
    <p class="font-semibold">{nearSeat.name}</p>
    <p class="text-xs text-muted-foreground">{m.livemap_near_seat_hint()}</p>
  {:else if neighbor != null}
    <p class="font-semibold">{stateName(neighbor)}</p>
    <p class="text-xs text-muted-foreground">{m.livemap_near_hint()}</p>
  {:else if chamber != null}
    {const list = $derived(chamber.areas.get(areaId) ?? [])}
    {#each list.slice(0, 2) as entry (entry.contest.id)}
      <p class="font-semibold">
        {entry.contest.isSpecial ? entry.contest.title : seatLabel(entry)}
      </p>
      {@render tipLines(
        entry.summary.race,
        entry.summary.totals,
        prior(entry.summary.race)?.shares ?? null,
        (entry.summary.settled ?? entry.summary.leader)?.id ?? null,
      )}
      <p class="text-xs text-muted-foreground">
        {verdict(
          entry.summary,
        )}{#if entry.held != null && entry.summary.total === 0}<span
            aria-hidden="true"
            >,
          </span>{m.night_state_held({ party: entry.held.short })}{/if}
      </p>
    {:else}
      <p class="text-xs text-muted-foreground">{m.livemap_not_here()}</p>
    {/each}
  {:else if nationTip != null}
    <p class="font-semibold">{nationTip.title}</p>
    {#if nationTip.lines.length === 0}
      <p class="text-xs text-muted-foreground">{m.night_nation_none()}</p>
    {:else if office === "house" && nationTip.lines.length > 1}
      {const groups = $derived(
        [
          ...Map.groupBy(
            nationTip.lines.flatMap((summary) => {
              const option = summary.settled ?? summary.leader;
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
        {@render tipLines(
          summary.race,
          summary.totals,
          prior(summary.race)?.shares ?? null,
          summary.leader?.id ?? null,
        )}
        <p class="text-xs text-muted-foreground">{verdict(summary)}</p>
      {/each}
    {/if}
  {:else if isSpreadShown}
    {const entry = $derived(spreadRows.get(areaId))}
    {#if entry != null}
      {@render countyTip(
        m.livemap_county_state({
          county: entry.name,
          state: stateName(entry.state),
        }),
        entry.race,
        entry.row,
        entry.counted,
        entry.pair,
      )}
    {/if}
  {:else}
    {const entry = $derived(rowByArea.get(areaId))}
    {@render countyTip(
      counties.get(areaId)?.name ?? "",
      race,
      entry?.row ?? null,
      entry?.counted ?? null,
      racePair,
    )}
  {/if}
{/snippet}

{#snippet mapNav()}
  {#if isNation}
    <Segmented
      options={viewOptions}
      value={isSpread ? "counties" : "states"}
      label={m.livemap_view()}
      size={wide.current ? "md" : "sm"}
      class="bg-card/95 shadow-menu backdrop-blur-sm"
      onValueChange={(value: string) =>
        update({ view: value === "counties" ? "counties" : null, pick: null })}
    />
  {:else}
    {const target = $derived(
      pickedSeat != null && chamber != null
        ? chamber.name
        : county == null
          ? m.livemap_nation()
          : race?.office === "house" && race.district != null
            ? seatName(race, stateName)
            : here,
    )}
    <button
      type="button"
      class="flex items-center gap-2 rounded-sm bg-card/95 ps-2.5 pe-2 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border backdrop-blur-sm keycap block-9 hover:bg-secondary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring pointer-coarse:block-10"
      aria-label={m.atlas_up({ place: target })}
      title={county == null && pickedSeat == null
        ? m.livemap_up_hint()
        : undefined}
      onclick={rise}
    >
      <ArrowLeftIcon
        class="block-4 inline-4 rtl:-scale-x-100"
        aria-hidden="true"
      />
      {target}
      <kbd class="pointer-coarse:hidden">esc</kbd>
    </button>
  {/if}
{/snippet}

{#snippet raceTabs()}
  {#if isNation && officeOptions.length > 1}
    <Segmented
      options={officeOptions}
      value={office}
      label={m.night_nation_views()}
      fill={officeOptions.length <= 4}
      class={cn({ "inline-full": officeOptions.length <= 4 })}
      onValueChange={(value) => update({ office: value, pick: null })}
    />
  {:else if !isNation && statewide.length + seats.length + chamberOf.size > 1}
    <RacePicker
      summaries={[
        ...statewide.filter((summary) => !chamberOf.has(summary.race.slug)),
        ...seats,
        ...chambers.flatMap((entry) =>
          entry.entries.map((item) => item.summary),
        ),
      ]}
      current={chamber != null
        ? (pickedEntries[0]?.contest.slug ?? null)
        : isOverview
          ? null
          : (race?.slug ?? null)}
      label={m.livemap_races()}
      hatchOf={(summary: Summary) => seatMark(summary, "lead")}
      isCompact
      overviews={[
        ...(hasOverview
          ? [
              {
                group: "house",
                label: m.livemap_overview(),
                isCurrent: isOverview,
                onpick: () => pickRace("house"),
              },
            ]
          : []),
        ...chambers.map((entry) => ({
          group: "legislature",
          label: entry.name,
          isCurrent: chamber?.id === entry.id,
          onpick: () => pickRace(entry.tier),
        })),
      ]}
      kinds={new Map(
        [...chamberOf.keys()].map((slug) => [slug, "legislature"]),
      )}
      onpick={pickSummary}
    >
      {#snippet search()}
        <div class="contents" onfocusin={loadNation}>
          <Search
            path={nationPath}
            summaries={everyRace}
            codes={everyState}
            labels={names}
            href={(target: string) =>
              href(
                `${election.slug.split("/")[0] ?? "us"}/${target}/${date}`,
                {},
              )}
            link={searchLink}
          />
        </div>
      {/snippet}
    </RacePicker>
  {/if}
{/snippet}

{#snippet modes(size: "sm" | "md")}
  {#if isNation && !isSpread}
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

{#snippet insetBoxes()}
  {#if insetSets.some((set) => set.places.length > 0)}
    <Insets
      sets={insetSets}
      {fills}
      {selected}
      bind:hovered
      stature={insetStature}
      nameOf={(name: string) =>
        name === "Alaska"
          ? stateName("ak")
          : name === "Hawaii"
            ? stateName("hi")
            : name}
      labelOf={(areaId: string) => {
        const entry = spreadRows.get(areaId);
        return entry == null
          ? (tipRace(areaId)?.title ?? "")
          : m.livemap_county_state({
              county: entry.name,
              state: stateName(entry.state),
            });
      }}
      onPick={pickArea}
      {tip}
    />
  {/if}
{/snippet}

{#snippet territoryChips()}
  {#if territories.length > 0}
    <div class="relative flex">
      {#if lit != null}
        <div
          class="pointer-events-none absolute inset-e-0 inset-be-[calc(100%+0.5rem)] z-30 grid gap-1.5 rounded-sm bg-popover/95 px-3 py-2.5 text-[0.8125rem] shadow-menu ring-1 ring-border backdrop-blur-sm inline-60 max-md:inset-s-0 max-md:inset-e-auto"
        >
          {@render tip(lit)}
        </div>
      {/if}
      <div
        class="pointer-events-auto flex overflow-hidden rounded-sm bg-card/95 shadow-menu ring-1 ring-border backdrop-blur-sm"
        role="group"
        aria-label={m.map_outlying()}
      >
        {#each territories as entry (entry.key)}
          <button
            type="button"
            class="flex shrink-0 items-center gap-1.5 border-s border-border px-2 text-xs font-semibold whitespace-nowrap block-7 first:border-s-0 hover:bg-secondary pointer-coarse:block-10"
            aria-label={stateName(entry.key)}
            onpointerenter={(event) => {
              if (event.pointerType === "mouse") {
                lit = entry.areaId;
              }
            }}
            onpointerleave={() => (lit = null)}
            onfocus={() => (lit = entry.areaId)}
            onblur={() => (lit = null)}
            onclick={() => open(entry.key, entry.seat)}
          >
            <span
              class="rounded-xs bg-(--party) block-2.5 inline-2.5"
              style:--party={entry.color}
              aria-hidden="true"
            ></span>
            {entry.key.toUpperCase()}
          </button>
        {/each}
      </div>
    </div>
  {/if}
{/snippet}

{#snippet legend()}
  <div class="grid gap-2">
    {#if isSwingable}
      <label
        class="flex items-center justify-between gap-2 text-xs font-semibold"
      >
        {m.map_mode_arrows()}
        <Switch
          checked={isArrows}
          onCheckedChange={(value: boolean) =>
            update({ arrows: value ? null : "off" }, false)}
        />
      </label>
    {/if}
    {#if swings.length > 0}
      <Legend
        kind="arrows"
        sides={arrowSides}
        reach={swingReach}
        text={m.night_legend_arrows()}
        blank={BLANK}
        isLater={isLaterShown}
      />
    {:else}
      <Legend
        kind={legendKind}
        {sides}
        bins={legendBins}
        text={legendText}
        blank={isNation && !isSpread ? WAITING : BLANK}
        isLater={isLaterShown}
      />
    {/if}
    {#if changing != null && found.pair != null}
      <SeatKey sides={found.pair} {changing} />
    {:else if [...painted.values()].some((fill) => fill.stripe != null)}
      <FlipKey
        about={rows.length > 0 || isSpreadShown
          ? m.night_flip_unit_about()
          : null}
      />
    {/if}
  </div>
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
              <span class="text-lg font-[760]">{seatsOf(tally)}</span>
            </span>
          {/each}
        </p>
      {/if}
    </div>
  {:else if current != null}
    <div class="flex items-start justify-between gap-3">
      <p class="line-clamp-2 text-[0.9375rem]/5 font-[720] text-pretty">
        {current.race.title}
      </p>
      <Button
        href={toLive(current.race.slug)}
        size="sm"
        variant="outline"
        class="shrink-0">{m.night_state_results()}</Button
      >
    </div>
    <p class="line-clamp-2 font-serif text-sm/snug text-pretty">
      {describe(current, closing(election, current.race), clock)}
    </p>
  {:else if chamber != null}
    <p class="text-[0.9375rem] font-[720]">{chamber.name}</p>
    <p class="font-figures text-sm text-muted-foreground tabular-nums">
      {m.night_state_seats_up({ count: count(chamber.up) })}
      <span aria-hidden="true">/</span>
      {m.night_chamber_majority({ count: count(chamber.seats.majority) })}
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
  {:else if chamber != null}
    <div class="grid grid-cols-[minmax(0,1fr)] gap-6">
      {#each pickedEntries as entry (entry.contest.id)}
        <div class="grid gap-3">
          <div class="flex items-start justify-between gap-3">
            <p class="text-[0.9375rem]/5 font-[720] text-pretty">
              {entry.contest.isSpecial ? entry.contest.title : seatLabel(entry)}
            </p>
            <Button
              href={toLive(entry.contest.slug)}
              size="sm"
              variant="outline"
              class="shrink-0">{m.night_state_results()}</Button
            >
          </div>
          <Card
            summary={entry.summary}
            held={entry.held}
            flip={turn(entry)}
            closes={closing(election, entry.summary.race)}
            limit={5}
          />
        </div>
      {/each}
      <Bar seats={chamber.seats} />
      <section class="grid gap-1.5">
        <h2 class="text-xs font-semibold text-muted-foreground">
          {m.night_state_district()}
        </h2>
        <ul class="grid">
          {#each chamber.entries.toSorted((a, b) => (urgency(a.summary, closing(election, a.summary.race))[0] ?? 0) - (urgency(b.summary, closing(election, b.summary.race))[0] ?? 0)) as entry (entry.contest.id)}
            {const area = $derived(entry.contest.area)}
            {const called = $derived(entry.summary.settled)}
            <li>
              <button
                type="button"
                aria-pressed={area != null && area === pickedSeat}
                class={cn(
                  "-mx-2 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2.5 rounded-sm px-2 py-1.5 text-start inline-[calc(100%+1rem)] focus-visible:bg-secondary",
                  {
                    "bg-secondary": area != null && area === pickedSeat,
                    "hover:bg-secondary": area == null || area !== pickedSeat,
                  },
                )}
                onclick={() => {
                  update({ seat: tail(entry.summary.race) });
                  if (area != null) {
                    canvas?.focus(area);
                  }
                }}
                onpointerenter={() => (hovered = area)}
                onpointerleave={() => (hovered = null)}
              >
                <span
                  class="rounded-xs ring-1 ring-border/60 block-3.5 inline-3.5"
                  style:background-color={chamberShade(entry, "lead")}
                  style:background-image={entryHatch(entry)?.stripes}
                ></span>
                <span class="grid min-inline-0">
                  <span
                    class="flex items-center gap-1.5 text-[0.8125rem] font-semibold"
                    ><span class="truncate"
                      >{entry.contest.isSpecial
                        ? entry.contest.title
                        : seatLabel(entry)}</span
                    >
                    {#if entry.isFlip && called != null}<span
                        class="shrink-0 text-[0.6875rem]"
                        style:color={tone(called)}>{m.night_flip()}</span
                      >{/if}</span
                  >
                  <span class="truncate text-xs text-muted-foreground"
                    >{verdict(entry.summary)}</span
                  >
                </span>
                <span
                  class="font-figures text-xs text-muted-foreground tabular-nums"
                  >{entry.summary.counted != null && entry.summary.total > 0
                    ? percent(entry.summary.counted, 0)
                    : ""}</span
                >
              </button>
            </li>
          {/each}
        </ul>
      </section>
    </div>
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
                style:background-image={seatMark(summary, "lead")?.stripes}
              ></span>
              <span class="grid min-inline-0">
                <span
                  class="flex items-center gap-1.5 text-[0.8125rem] font-semibold"
                  >{seatName(summary.race, stateName)}
                  {#if flip != null}<Flip
                      flip={flip.flip}
                      from={flip.from}
                      to={flip.to}
                      isCompact={true}
                    />{/if}</span
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
    <div class="grid grid-cols-[minmax(0,1fr)] gap-6">
      <RaceCard
        summary={current}
        held={holding(control, current.race)}
        flip={flipped(found.seats.get(current.race.id), found.pair, control)}
        closes={closing(election, current.race)}
        {clock}
        {forecasts}
      />
      {#if forecast != null}
        <Withheld isOff={!forecasts}>
          <Dial
            options={current.race.options}
            pair={racePair}
            {estimate}
            history={forecast.history}
            counted={forecast.counted}
            isWaiting={false}
          />
        </Withheld>
      {/if}
      {#if current.race.isMeasure !== true}
        <Cycle
          path={current.race.slug}
          options={current.race.options}
          totals={current.totals}
          total={current.total}
          counted={current.counted}
          estimate={forecasts ? current.estimate : null}
        />
      {/if}
      <CountyList
        {rows}
        isDistrict={isDistricted(current.race.state)}
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
  class="fixed inset-0 z-40 grid grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] bg-card text-foreground"
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

  {#if !isMounted}
    <div
      class="grid grid-cols-[minmax(0,1fr)] min-block-0 md:grid-cols-[minmax(0,1fr)_min(25rem,36vw)]"
      aria-busy="true"
    >
      <div
        class="relative animate-pulse bg-muted min-block-0 motion-reduce:animate-none"
      >
        <Skeleton
          class="absolute inset-s-3 inset-bs-3 rounded-sm bg-card block-9 inline-40"
        />
        <div
          class="absolute inset-x-0 inset-be-0 grid gap-3 rounded-t-lg bg-card px-4 pbs-3 pbe-6 shadow-menu ring-1 ring-border md:hidden"
        >
          <Skeleton class="mx-auto rounded-full block-1 inline-9" />
          <Skeleton class="rounded-sm block-9" />
          <Skeleton class="rounded-xs block-5 inline-2/3" />
          <Skeleton class="rounded-xs block-4 inline-5/6" />
        </div>
      </div>
      <div class="grid content-start gap-4 border-s bg-card p-4 max-md:hidden">
        <Skeleton class="rounded-sm block-9" />
        <Skeleton class="rounded-xs block-5 inline-2/3" />
        <Skeleton class="rounded-xs block-4 inline-5/6" />
        <div class="grid gap-3 pbs-2">
          {#each [0, 1, 2] as row (row)}
            <div class="grid grid-cols-[1fr_auto] items-center gap-3">
              <Skeleton class="rounded-xs block-4" />
              <Skeleton class="rounded-xs block-4 inline-12" />
            </div>
            <Skeleton class="rounded-xs block-2" />
          {/each}
        </div>
        <Skeleton class="rounded-sm block-32" />
      </div>
    </div>
  {:else}
    <div
      class={cn("grid min-block-0", {
        "grid-cols-[minmax(0,1fr)_min(25rem,36vw)]": wide.current,
        "grid-cols-[minmax(0,1fr)]": !wide.current,
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
            {swings}
            reach={swingReach}
            {labels}
            {selected}
            bind:hovered
            {pulses}
            inset={{
              top: controlsHeight + 12,
              bottom: wide.current
                ? isNationFramed
                  ? 0
                  : legendHeight + 12
                : sheet,
              end: wide.current ? 48 : 0,
            }}
            label={isNation
              ? m.night_nation_map({ view: OFFICE_NAMES[office]() })
              : (current?.race.title ?? m.night_nation_house())}
            onPick={pickArea}
            around={nearby}
            {isAlone}
            {aim}
            {seat}
            {hold}
            {tip}
          />
        {/if}

        <div
          class="pointer-events-none absolute inset-x-3 inset-bs-3 z-20 flex items-start justify-between gap-2"
        >
          <div class="pointer-events-auto" bind:clientHeight={controlsHeight}>
            {@render mapNav()}
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
                class={cn(
                  "grid place-items-center border-bs block-9 inline-9",
                  {
                    "bg-foreground text-card": prefs.isBasemap,
                    "hover:bg-secondary": !prefs.isBasemap,
                  },
                )}
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
                {loading}
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
            class="pointer-events-none absolute inset-s-3 inset-be-3 z-20 grid justify-items-start gap-2"
          >
            {@render insetBoxes()}
            <div
              class="pointer-events-auto grid gap-3 rounded-sm bg-card/95 p-3 shadow-menu ring-1 ring-border backdrop-blur-sm inline-[min(24rem,calc(100vw-min(25rem,36vw)-6rem))]"
              bind:clientHeight={legendHeight}
            >
              {@render modes("sm")}
              {@render legend()}
            </div>
          </div>
          <div class="pointer-events-none absolute inset-e-15 inset-be-3 z-20">
            {@render territoryChips()}
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
          <div
            class="pointer-events-none absolute inset-s-3 z-20 grid justify-items-start gap-1.5"
            style:inset-block-end="{sheet + 12}px"
          >
            {@render territoryChips()}
            {@render insetBoxes()}
          </div>
          <Drawer
            {room}
            bind:detent
            bind:height={sheet}
            label={m.atlas_results()}
          >
            {#snippet head()}
              <div
                class="grid grid-cols-[minmax(0,1fr)] gap-3 px-4 pbs-1 pbe-3"
              >
                {#if isNation}
                  {@render raceTabs()}
                {/if}
                <div class="grid gap-1">
                  {@render summaryHead()}
                </div>
              </div>
            {/snippet}
            <div class="grid grid-cols-[minmax(0,1fr)] gap-5 px-4 pbe-8">
              {#if !isNation}
                {@render raceTabs()}
              {/if}
              {@render panel()}
              {@render modes("sm")}
              {@render legend()}
              {@render footer()}
            </div>
          </Drawer>
        {/if}
      </main>

      {#if wide.current}
        <aside
          class="grid auto-rows-max grid-cols-[minmax(0,1fr)] content-start gap-5 overflow-y-auto overscroll-contain border-s bg-card p-4"
          aria-label={m.atlas_results()}
        >
          {@render raceTabs()}
          {@render panel()}
          {@render footer()}
        </aside>
      {/if}
    </div>
  {/if}
</div>
