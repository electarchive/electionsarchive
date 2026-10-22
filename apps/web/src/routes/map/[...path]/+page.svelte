<script lang="ts" module>
  import type { PageData } from "./$types";

  const cache: Record<string, Promise<PageData["atlas"]> | undefined> = {};
</script>

<script lang="ts">
  import type * as Maplibre from "maplibre-gl";
  import { afterNavigate, goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import MinusIcon from "@lucide/svelte/icons/minus";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import ScanIcon from "@lucide/svelte/icons/scan";
  import SlidersHorizontalIcon from "@lucide/svelte/icons/sliders-horizontal";
  import XIcon from "@lucide/svelte/icons/x";
  import { mode } from "mode-watcher";
  import { untrack } from "svelte";
  import { on } from "svelte/events";
  import { SvelteURLSearchParams } from "svelte/reactivity";
  import { fade } from "svelte/transition";
  import Monogram from "#lib/components/monogram.svelte";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { Switch } from "#lib/components/ui/switch/index.js";
  import { count, date, percent, points } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import Histogram from "./histogram.svelte";

  type Sheet = PageData["atlas"];

  const { data }: { data: PageData } = $props();

  const MID = 4.4;
  const FINE = 7;
  const BINS = [0.05, 0.1, 0.2];
  const STRENGTHS = [34, 54, 76, 100];
  const SHARES = [0.3, 0.4, 0.5, 0.6, 0.7];
  const STEPS = [14, 30, 48, 66, 84, 100];
  const SWINGS = [0.02, 0.05, 0.1];
  const DIM = "color-mix(in oklab, var(--muted-foreground) 11%, var(--card))";
  const VIEWS = ["winner", "share", "votes", "swing", "flips"] as const;

  const uid = $props.id();
  const calm =
    typeof matchMedia !== "undefined" &&
    matchMedia("(prefers-reduced-motion: reduce)").matches;
  const locate = (url: {
    pathname: string;
    searchParams: { get: (key: string) => string | null };
  }) =>
    [
      decodeURI(url.pathname).slice(`/map/${data.path}`.length + 1),
      url.searchParams.get("level") ?? "",
      url.searchParams.get("vs") ?? "",
      url.searchParams.get("group") === "alliance" ? "alliance" : "",
    ].join("|");
  let href = $state(untrack(() => page.url.href));
  const asked = $derived(locate(new URL(href)));
  let loaded = $derived({
    sheet: data.atlas,
    key: untrack(() => locate(page.url)),
  });
  const sheet = $derived(loaded.sheet);
  const grouped = $derived(asked.endsWith("|alliance"));
  let isLoading = $state(false);
  let pending = $state(0);

  const query = untrack(() => page.url.searchParams);
  let view = $state<(typeof VIEWS)[number]>(
    VIEWS.find((choice) => choice === query.get("color")) ?? "winner",
  );
  let who = $state<string | null>(query.get("who"));
  let range = $state<[number, number] | null>(
    untrack(() => {
      const [low, high] = (query.get("range") ?? "")
        .split(",")
        .map((part) => Number(part) / 1000);
      return low == null ||
        high == null ||
        !Number.isFinite(low) ||
        !Number.isFinite(high) ||
        query.get("range") == null
        ? null
        : [low, high];
    }),
  );
  let only = $state<string | null>(query.get("only"));
  let isFlippedOnly = $state(query.get("flipped") === "1");
  let hovered = $state<number | null>(null);
  let pinned = $state<number | null>(null);
  let pointer = $state<{ x: number; y: number } | null>(null);
  let term = $state("");
  let isKeyOpen = $state(false);
  let credits = $state<Array<string>>([]);
  let map = $state.raw<Maplibre.Map | null>(null);
  let detail = $state.raw<
    Record<string, { set: Sheet; source: string; rank: number }>
  >({});
  let lookup: Record<number, { set: Sheet; i: number; source: string }> = {};
  let seen = $state(0);
  let search = $state<HTMLInputElement | null>(null);
  let labels = $state.raw<
    Array<{ k: number; name: string; x: number; y: number }>
  >([]);
  let isQueued = false;

  const isCompared = $derived(
    sheet.target != null &&
      (sheet.places.redrawn.some((value) => value != null) ||
        sheet.places.before.some((column) =>
          column.some((value) => value != null),
        )),
  );
  const shown = $derived(
    !isCompared && (view === "swing" || view === "flips") ? "winner" : view,
  );
  const needsBefore = $derived(shown === "swing" || shown === "flips");
  const isEstimated = $derived(
    isCompared &&
      (sheet.estimated ||
        Object.values(detail).some((held) => held.set.estimated)),
  );
  const first = $derived(sheet.legend[0] ?? null);
  const second = $derived(sheet.legend[1] ?? null);
  const whom = $derived(
    shown === "share"
      ? (sheet.legend.find((item) => item.key === who) ?? first)
      : null,
  );
  const isParts = $derived(
    sheet.parts.length > 0 && sheet.places.k.includes(sheet.parts[0]?.k ?? -1),
  );
  const mid = $derived(
    isParts
      ? (sheet.levels.findLast(
          (level) => level.tiles != null && level.code !== sheet.level,
        ) ?? null)
      : null,
  );
  const current = $derived(
    sheet.levels.find((level) => level.code === sheet.level) ?? null,
  );
  const year = $derived(
    sheet.targets
      .find((target) => target.id === sheet.target)
      ?.date.slice(0, 4) ?? null,
  );
  const unmatched = $derived.by(() => {
    let redrawn = 0;
    let missing = 0;
    if (isCompared) {
      for (const [i] of sheet.places.k.entries()) {
        if (read(sheet, i).isUnmatched) {
          if (sheet.places.redrawn[i] == null) {
            missing += 1;
          } else {
            redrawn += 1;
          }
        }
      }
    }
    return { redrawn, missing };
  });
  const tones = $derived(
    Object.fromEntries(
      sheet.legend.map((item) => [item.key, swatch(item.color, item.key)]),
    ),
  );
  const scale = $derived(
    shown === "share"
      ? { domain: [0, 1] as [number, number], step: 0.05 }
      : shown === "swing"
        ? { domain: [-0.3, 0.3] as [number, number], step: 0.025 }
        : { domain: [-0.8, 0.8] as [number, number], step: 0.05 },
  );

  function hue(set: Sheet, index: number | null) {
    const item = index == null ? null : set.legend[index];
    if (item == null) {
      return "var(--muted-foreground)";
    }
    return tones[item.key] ?? swatch(item.color, item.key);
  }

  function mix(tone: string, strength: number) {
    return `color-mix(in oklab, ${tone} ${strength}%, var(--map-low))`;
  }

  function step(value: number, edges: Array<number>) {
    let index = 0;
    for (const edge of edges) {
      if (value >= edge) {
        index += 1;
      }
    }
    return index;
  }

  function read(set: Sheet, i: number) {
    const columns = set.places.shares;
    const at = (key: string | undefined) => {
      const index = set.legend.findIndex((item) => item.key === key);
      return index < 0 ? 0 : (columns[index]?.[i] ?? 0) / 10_000;
    };
    const was = (key: string | undefined) => {
      const index = set.legend.findIndex((item) => item.key === key);
      const value = index < 0 ? null : (set.places.before[index]?.[i] ?? null);
      return value == null ? null : value / 10_000;
    };
    let top = 0;
    let next = 0;
    for (const column of columns) {
      const value = (column[i] ?? 0) / 10_000;
      if (value > top) {
        next = top;
        top = value;
      } else if (value > next) {
        next = value;
      }
    }
    const margin = at(first?.key) - at(second?.key);
    const then = [was(first?.key), was(second?.key)];
    const leader = set.places.leader[i] ?? null;
    const prior = set.places.prior[i] ?? null;
    const swing =
      then[0] == null || then[1] == null ? null : margin - (then[0] - then[1]);
    const value =
      shown === "share"
        ? at(whom?.key)
        : shown === "swing"
          ? swing
          : leader == null
            ? null
            : margin;
    return {
      leader,
      lead: top - next,
      margin,
      swing,
      value,
      isFlip: prior != null && leader != null && prior !== leader,
      isUnmatched:
        isCompared &&
        leader != null &&
        set.places.before.every((column) => column[i] == null),
    };
  }

  function admits(set: Sheet, reading: ReturnType<typeof read>) {
    const [low, high] = range ?? [-Infinity, Infinity];
    const clamped =
      reading.value == null
        ? null
        : Math.min(scale.domain[1], Math.max(scale.domain[0], reading.value));
    return (
      (range == null ||
        (clamped != null && clamped >= low - 1e-9 && clamped <= high + 1e-9)) &&
      (only == null ||
        (reading.leader != null && set.legend[reading.leader]?.key === only)) &&
      (!isFlippedOnly || !isCompared || reading.isFlip)
    );
  }

  function tone(set: Sheet, i: number) {
    const reading = read(set, i);
    if (reading.leader == null) {
      return DIM;
    }
    if (!admits(set, reading)) {
      return DIM;
    }
    if (shown === "share") {
      return mix(
        tones[whom?.key ?? ""] ?? "var(--muted-foreground)",
        STEPS[step(reading.value ?? 0, SHARES)] ?? 100,
      );
    }
    if (shown === "swing") {
      if (reading.swing == null) {
        return DIM;
      }
      return mix(
        tones[(reading.swing >= 0 ? first : second)?.key ?? ""] ??
          "var(--muted-foreground)",
        STRENGTHS[step(Math.abs(reading.swing), SWINGS)] ?? 100,
      );
    }
    if (shown === "flips") {
      if (reading.isUnmatched) {
        return DIM;
      }
      return mix(hue(set, reading.leader), reading.isFlip ? 100 : 16);
    }
    return mix(
      hue(set, reading.leader),
      set.places.valid[i] === 0
        ? 100
        : (STRENGTHS[step(reading.lead, BINS)] ?? 100),
    );
  }

  const bars = $derived.by(() => {
    const [low, high] = scale.domain;
    const total = Math.round((high - low) / scale.step);
    const tally = Array.from<number>({ length: total }).fill(0);
    let admitted = 0;
    let measured = 0;
    for (const [i] of sheet.places.k.entries()) {
      const reading = read(sheet, i);
      if (reading.value == null) {
        continue;
      }
      measured += 1;
      if (admits(sheet, reading)) {
        admitted += 1;
      }
      const at = Math.min(
        total - 1,
        Math.max(0, Math.floor((reading.value - low) / scale.step)),
      );
      tally[at] = (tally[at] ?? 0) + 1;
    }
    return {
      admitted,
      measured,
      bars: tally.map((tallied, index) => {
        const middle = low + (index + 0.5) * scale.step;
        const color =
          shown === "share"
            ? mix(
                tones[whom?.key ?? ""] ?? "var(--muted-foreground)",
                STEPS[step(middle, SHARES)] ?? 100,
              )
            : mix(
                tones[(middle >= 0 ? first : second)?.key ?? ""] ??
                  "var(--muted-foreground)",
                STRENGTHS[
                  step(Math.abs(middle), shown === "swing" ? SWINGS : BINS)
                ] ?? 100,
              );
        return {
          count: tallied,
          color,
          isShown:
            range == null ||
            (middle >= range[0] - scale.step / 2 &&
              middle <= range[1] + scale.step / 2),
        };
      }),
    };
  });
  const ticks = $derived.by(() => {
    const [low, high] = scale.domain;
    if (shown === "share") {
      return [
        { at: 0, label: percent(0).replace(/[.,]0/u, "") },
        { at: 0.5, label: percent(0.5).replace(/[.,]0/u, "") },
        { at: 1, label: percent(1).replace(/[.,]0/u, "") },
      ];
    }
    const name = (item: typeof first) =>
      item?.label.replace(/ \([^)]*\)$/u, "") ?? "";
    return [
      {
        at: low,
        label: m.atlas_lead({
          name: name(second),
          points: String(Math.round(-low * 100)),
        }),
      },
      { at: 0, label: shown === "swing" ? m.atlas_no_shift() : m.atlas_even() },
      {
        at: high,
        label: m.atlas_lead({
          name: name(first),
          points: String(Math.round(high * 100)),
        }),
      },
    ];
  });
  const leads = $derived(
    Map.groupBy(
      sheet.places.leader.filter((leader) => leader != null),
      (leader) => leader,
    )
      .entries()
      .map(([index, won]) => ({ index, won: won.length }))
      .toArray()
      .toSorted((a, b) => b.won - a.won),
  );
  const focus = $derived(pinned ?? hovered);
  const entry = $derived.by(() => {
    void seen;
    return focus == null ? null : (lookup[focus] ?? null);
  });
  const glance = $derived.by(() => {
    void seen;
    if (pinned == null || hovered == null || hovered === pinned) {
      return null;
    }
    const near = lookup[hovered];
    const far = lookup[pinned];
    if (near == null || far == null) {
      return null;
    }
    const here = read(near.set, near.i);
    const there = read(far.set, far.i);
    const gap = here.margin - there.margin;
    return {
      name: near.set.places.name[near.i] ?? "",
      leader: here.leader == null ? null : near.set.legend[here.leader],
      tone: hue(near.set, here.leader),
      lead: here.lead,
      gap,
      side: gap >= 0 ? first : second,
      against: far.set.places.name[far.i] ?? "",
    };
  });
  const found = $derived.by(() => {
    void seen;
    const needle = term.trim().toLocaleLowerCase();
    if (needle.length < 2) {
      return [];
    }
    const starts: Array<{ k: number; name: string; where: string }> = [];
    const contains: Array<{ k: number; name: string; where: string }> = [];
    for (const set of [
      sheet,
      ...Object.values(detail).map((tier) => tier.set),
    ]) {
      for (const [i, name] of set.places.name.entries()) {
        const lower = name.toLocaleLowerCase();
        const at = lower.indexOf(needle);
        if (at < 0) {
          continue;
        }
        const row = {
          k: set.places.k[i] ?? 0,
          name,
          where: set === sheet ? "" : set.scope.name,
        };
        if (at === 0) {
          starts.push(row);
        } else {
          contains.push(row);
        }
        if (starts.length >= 8) {
          break;
        }
      }
    }
    return [...starts, ...contains].slice(0, 8);
  });

  async function request(
    where: string,
    level: string | null,
    vs: string | null,
  ) {
    const [, , , group = ""] = asked.split("|");
    const key = [data.contest, where, level ?? "", vs ?? "", group].join("|");
    const known = cache[key];
    if (known != null) {
      return known;
    }
    const fetched = orpc.contests.atlas.call({
      id: data.contest,
      unit: where === "" ? undefined : where,
      level: level ?? undefined,
      vs: vs ?? undefined,
      group: group === "" ? undefined : group,
    });
    cache[key] = fetched;
    fetched.catch(() => {
      if (cache[key] === fetched) {
        cache[key] = undefined;
      }
    });
    return fetched;
  }

  function move(
    where: string,
    level: string | null,
    vs: string | null,
    group: string | null = grouped ? "alliance" : null,
  ) {
    const [now = ""] = asked.split("|");
    const path = [data.path, where].filter((part) => part !== "").join("/");
    const params = new SvelteURLSearchParams(location.search);
    for (const [key, value] of [
      ["level", level],
      ["vs", vs],
      ["group", group],
    ] as const) {
      if (value == null) {
        params.delete(key);
      } else {
        params.set(key, value);
      }
    }
    if (where !== now) {
      params.delete("range");
      params.delete("only");
      range = null;
      only = null;
      pinned = null;
      hovered = null;
    }
    const query = params.toString();
    const target =
      query === ""
        ? resolve("/map/[...path]", { path })
        : resolve(`/map/[...path]?${query}`, { path });
    href = new URL(target, location.origin).href;
    void goto(target, { shallow: true, replace: where === now, state: {} });
  }

  function scope(where: string) {
    const [, , vs = ""] = asked.split("|");
    move(where, null, vs === "" ? null : vs);
  }

  $effect(() => {
    const key = [data.contest, untrack(() => locate(page.url))].join("|");
    cache[key] ??= Promise.resolve(data.atlas);
  });

  $effect(() => {
    const wanted = asked;
    if (wanted === untrack(() => loaded.key)) {
      return;
    }
    const [where = "", level = "", vs = ""] = wanted.split("|");
    isLoading = true;
    request(where, level === "" ? null : level, vs === "" ? null : vs).then(
      (next) => {
        if (asked === wanted) {
          loaded = { sheet: next, key: wanted };
          isLoading = false;
        }
      },
      () => {
        if (asked === wanted) {
          isLoading = false;
        }
      },
    );
  });

  $effect(() => {
    keep({
      color: shown === "winner" ? null : shown,
      who: shown === "share" ? (whom?.key ?? null) : null,
      range:
        range == null
          ? null
          : range.map((value) => Math.round(value * 1000)).join(","),
      only,
      flipped: isFlippedOnly && isCompared ? "1" : null,
    });
  });

  function probe(host: HTMLElement) {
    const span = document.createElement("span");
    host.append(span);
    const context = document
      .createElement("canvas")
      .getContext("2d", { willReadFrequently: true });
    const known: Record<string, string> = {};
    const color = (css: string) => {
      const hit = known[css];
      if (hit != null) {
        return hit;
      }
      span.style.color = css;
      let value = getComputedStyle(span).color;
      if (context != null) {
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = value;
        context.fillRect(0, 0, 1, 1);
        const [r = 0, g = 0, b = 0, a = 255] = context.getImageData(
          0,
          0,
          1,
          1,
        ).data;
        value = `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
      }
      known[css] = value;
      return value;
    };
    return { color, done: () => span.remove() };
  }

  let layers: Array<string> = [];
  let sources: Array<string> = [];
  let groups: Array<{ rank: number; first: string }> = [];
  let painted: Record<string, string> = {};
  let marked: Array<{ source: string; k: number }> = [];

  function credit(tiles: string) {
    void import("pmtiles").then(async ({ PMTiles }) => {
      const meta = (await new PMTiles(
        `${globalThis.location.origin}/tiles/${tiles}.pmtiles`,
      ).getMetadata()) as { attribution?: unknown };
      const text = String(meta.attribution ?? "");
      credits = [
        ...new Set([
          ...credits,
          ...text
            .split(text.includes("\n") ? "\n" : "; ")
            .filter((part) => part.trim() !== ""),
        ]),
      ];
    });
  }

  function source(gl: Maplibre.Map, tiles: string) {
    const id = `t:${tiles}`;
    if (gl.getSource(id) == null) {
      gl.addSource(id, {
        type: "vector",
        url: `pmtiles://${globalThis.location.origin}/tiles/${tiles}.pmtiles`,
      });
      sources.push(id);
      credit(tiles);
    }
    return id;
  }

  function tier(
    gl: Maplibre.Map,
    tiles: string,
    rank: number,
    from: number,
    isDense: boolean,
  ) {
    const id = source(gl, tiles);
    const fill = `fill:${id}`;
    if (gl.getLayer(fill) != null) {
      return id;
    }
    const reader = probe(gl.getContainer());
    const paper = reader.color("var(--card)");
    const ink = reader.color("var(--foreground)");
    reader.done();
    const before =
      groups
        .toSorted((a, b) => a.rank - b.rank)
        .find((group) => group.rank > rank)?.first ?? "anchor-fills";
    gl.addLayer(
      {
        id: fill,
        type: "fill",
        source: id,
        "source-layer": "areas",
        minzoom: from,
        paint: {
          "fill-color": [
            "coalesce",
            ["feature-state", "color"],
            "rgba(0, 0, 0, 0)",
          ],
        },
      },
      before,
    );
    gl.addLayer(
      {
        id: `hatch:${id}`,
        type: "fill",
        source: id,
        "source-layer": "areas",
        minzoom: from,
        paint: {
          "fill-pattern": "hatch",
          "fill-opacity": [
            "case",
            ["boolean", ["feature-state", "hatched"], false],
            1,
            0,
          ],
        },
      },
      before,
    );
    gl.addLayer(
      {
        id: `seam:${id}`,
        type: "line",
        source: id,
        "source-layer": "areas",
        minzoom: from,
        paint: {
          "line-color": paper,
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            3,
            isDense ? 0.08 : 0.6,
            6,
            isDense ? 0.2 : 0.9,
            9,
            isDense ? 0.55 : 1.2,
            13,
            1.2,
          ],
          "line-opacity": [
            "case",
            ["boolean", ["feature-state", "on"], false],
            1,
            0,
          ],
        },
      },
      before,
    );
    gl.addLayer(
      {
        id: `edge:${id}`,
        type: "line",
        source: id,
        "source-layer": "areas",
        minzoom: rank === 0 ? MID : FINE,
        paint: {
          "line-color": paper,
          "line-width": rank === 0 ? 2 : 1.25,
          "line-opacity": [
            "case",
            ["boolean", ["feature-state", "on"], false],
            1,
            0,
          ],
        },
      },
      "anchor-edges",
    );
    gl.addLayer(
      {
        id: `focus:${id}`,
        type: "line",
        source: id,
        "source-layer": "areas",
        minzoom: from,
        paint: {
          "line-color": ink,
          "line-width": [
            "case",
            ["boolean", ["feature-state", "focus"], false],
            2.4,
            1.2,
          ],
          "line-opacity": [
            "case",
            [
              "any",
              ["boolean", ["feature-state", "focus"], false],
              ["boolean", ["feature-state", "hover"], false],
            ],
            1,
            0,
          ],
        },
      },
      "anchor-focus",
    );
    layers.push(fill, `hatch:${id}`, `seam:${id}`, `edge:${id}`, `focus:${id}`);
    groups.push({ rank, first: fill });
    return id;
  }

  function index(gl: Maplibre.Map, set: Sheet, id: string, rank: number) {
    for (const [i, k] of set.places.k.entries()) {
      lookup[k] = { set, i, source: id };
    }
    seen += 1;
    const dots = `dots:${id}:${set.scope.k}:${set.level ?? ""}`;
    if (gl.getSource(dots) != null) {
      return;
    }
    gl.addSource(dots, {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: set.places.k.flatMap((k, i) => {
          const x = set.places.x[i];
          const y = set.places.y[i];
          return x == null || y == null
            ? []
            : [
                {
                  type: "Feature" as const,
                  id: k,
                  properties: { votes: set.places.valid[i] ?? 0 },
                  geometry: { type: "Point" as const, coordinates: [x, y] },
                },
              ];
        }),
      },
    });
    sources.push(dots);
    const reader = probe(gl.getContainer());
    const paper = reader.color("var(--card)");
    reader.done();
    const size: Maplibre.ExpressionSpecification = [
      "coalesce",
      ["feature-state", "size"],
      0,
    ];
    gl.addLayer(
      {
        id: dots,
        type: "circle",
        source: dots,
        minzoom: rank === 0 ? 0 : rank === 1 ? MID : FINE,
        maxzoom: rank === 0 ? (mid == null ? 24 : MID) : rank === 1 ? FINE : 24,
        layout: { "circle-sort-key": ["-", 0, ["get", "votes"]] },
        paint: {
          "circle-radius": [
            "interpolate",
            ["exponential", 2],
            ["zoom"],
            0,
            ["*", size, 2 ** -4],
            22,
            ["*", size, 2 ** 18],
          ],
          "circle-color": [
            "coalesce",
            ["feature-state", "dot"],
            "rgba(0, 0, 0, 0)",
          ],
          "circle-opacity": 0.9,
          "circle-stroke-color": paper,
          "circle-stroke-width": ["case", [">", size, 0], 0.6, 0],
        },
      },
      "anchor-edges",
    );
    layers.push(dots);
  }

  function build(gl: Maplibre.Map, next: Sheet) {
    for (const id of layers.toReversed()) {
      if (gl.getLayer(id) != null) {
        gl.removeLayer(id);
      }
    }
    for (const id of sources) {
      if (gl.getSource(id) != null) {
        gl.removeSource(id);
      }
    }
    layers = [];
    sources = [];
    groups = [];
    painted = {};
    marked = [];
    lookup = {};
    credits = [];
    const reader = probe(gl.getContainer());
    if (next.outline != null) {
      const id = source(gl, next.outline);
      gl.addLayer(
        {
          id: "context",
          type: "fill",
          source: id,
          "source-layer": "areas",
          paint: {
            "fill-color": reader.color(
              "color-mix(in oklab, var(--muted-foreground) 9%, var(--card))",
            ),
          },
        },
        "anchor-fills",
      );
      gl.addLayer(
        {
          id: "context-seam",
          type: "line",
          source: id,
          "source-layer": "areas",
          paint: { "line-color": reader.color("var(--card)"), "line-width": 1 },
        },
        "anchor-fills",
      );
      layers.push("context", "context-seam");
      if (next.trail.length > 1) {
        gl.addLayer(
          {
            id: "scope",
            type: "line",
            source: id,
            "source-layer": "areas",
            filter: ["==", ["id"], next.scope.k],
            paint: {
              "line-color": reader.color("var(--foreground)"),
              "line-width": 1.5,
            },
          },
          "anchor-focus",
        );
        layers.push("scope");
      }
    }
    reader.done();
    const level = next.levels.find((entry) => entry.code === next.level);
    if (level?.tiles != null) {
      index(
        gl,
        next,
        tier(gl, level.tiles, 0, 0, next.places.k.length > 400),
        0,
      );
    }
  }

  function paint(gl: Maplibre.Map, set: Sheet, id: string, mark: string) {
    if (painted[`${id}|${set.scope.k}|${set.level}`] === mark) {
      return;
    }
    painted[`${id}|${set.scope.k}|${set.level}`] = mark;
    const reader = probe(gl.getContainer());
    const dots = `dots:${id}:${set.scope.k}:${set.level ?? ""}`;
    const isDotted = shown === "votes" && gl.getSource(dots) != null;
    const ground = reader.color(
      "color-mix(in oklab, var(--muted-foreground) 15%, var(--card))",
    );
    for (const [i, k] of set.places.k.entries()) {
      const color = reader.color(tone(set, i));
      gl.setFeatureState(
        { source: id, sourceLayer: "areas", id: k },
        {
          color: isDotted ? ground : color,
          on: true,
          hatched: needsBefore && (set.estimated || read(set, i).isUnmatched),
        },
      );
      if (gl.getSource(dots) != null) {
        gl.setFeatureState(
          { source: dots, id: k },
          {
            dot: color,
            size: isDotted ? 0.015 * Math.sqrt(set.places.valid[i] ?? 0) : 0,
          },
        );
      }
    }
    reader.done();
  }

  function deepen() {
    const gl = map;
    if (gl == null || !isParts) {
      return;
    }
    const zoom = gl.getZoom();
    const [, , compared = ""] = asked.split("|");
    const vs = compared === "" ? null : compared;
    const base = sheet;
    const wanted: Array<{ key: string; where: string; level: string | null }> =
      [];
    if (mid?.tiles != null && zoom >= MID - 0.5) {
      wanted.push({ key: "mid", where: "", level: mid.code });
    }
    if (zoom >= FINE - 0.6) {
      const ids = gl
        .queryRenderedFeatures({ layers: groups.map((group) => group.first) })
        .map((feature) => feature.id);
      const middle = gl.getCenter();
      const near = sheet.parts
        .filter((part) => ids.includes(part.k))
        .map((part) => {
          const i = sheet.places.k.indexOf(part.k);
          const x = sheet.places.x[i] ?? middle.lng;
          const y = sheet.places.y[i] ?? middle.lat;
          return { part, gap: Math.hypot(x - middle.lng, y - middle.lat) };
        })
        .toSorted((a, b) => a.gap - b.gap)
        .slice(0, 8);
      for (const { part } of near) {
        wanted.push({
          key: `fine:${part.unit}`,
          where: part.unit,
          level: null,
        });
      }
    }
    for (const want of wanted) {
      if (detail[want.key] != null) {
        continue;
      }
      pending += 1;
      void request(want.where, want.level, vs)
        .then((set) => {
          const level = set.levels.find((entry) => entry.code === set.level);
          if (
            sheet !== base ||
            map !== gl ||
            level?.tiles == null ||
            detail[want.key] != null ||
            (want.key !== "mid" && set.level === mid?.code)
          ) {
            return;
          }
          const rank = want.key === "mid" ? 1 : 2;
          const id = tier(gl, level.tiles, rank, rank === 1 ? MID : FINE, true);
          index(gl, set, id, rank);
          detail = { ...detail, [want.key]: { set, source: id, rank } };
        })
        .finally(() => {
          pending -= 1;
        });
    }
  }

  function frame(gl: Maplibre.Map, set: Sheet, isAnimated: boolean) {
    const box = set.frames[0];
    if (box == null) {
      return;
    }
    gl.fitBounds([box.west, box.south, box.east, box.north], {
      padding: room(),
      animate: isAnimated && !calm,
      duration: 900,
    });
  }

  function room() {
    const width = globalThis.innerWidth;
    const side = Math.round(Math.min(320, width * 0.16));
    return width >= 768
      ? { top: 64, right: side, bottom: 24, left: side }
      : { top: 64, right: 16, bottom: 120, left: 16 };
  }

  function fly(k: number) {
    const gl = map;
    const hit = lookup[k];
    if (gl == null || hit == null) {
      return;
    }
    const x = hit.set.places.x[hit.i];
    const y = hit.set.places.y[hit.i];
    const box = hit.set.frames[0];
    if (x == null || y == null) {
      return;
    }
    const base =
      box == null
        ? gl.getZoom()
        : (gl.cameraForBounds([box.west, box.south, box.east, box.north], {
            padding: room(),
          })?.zoom ?? gl.getZoom());
    const zoom = Math.min(
      15,
      Math.max(
        gl.getZoom(),
        base + Math.log2(Math.sqrt(hit.set.places.k.length)) - 0.4,
      ),
    );
    pinned = k;
    gl.flyTo({ center: [x, y], zoom, duration: calm ? 0 : 1100 });
  }

  function zoomTo(k: number) {
    const gl = map;
    const hit = lookup[k];
    if (gl == null || hit == null) {
      return;
    }
    const box = [Infinity, Infinity, -Infinity, -Infinity];
    for (const part of gl.querySourceFeatures(hit.source, {
      sourceLayer: "areas",
      filter: ["==", ["id"], k],
    })) {
      for (const pair of JSON.stringify(part.geometry).matchAll(
        /\[([\d.e-]+),([\d.e-]+)\]/g,
      )) {
        const x = Number(pair[1]);
        const y = Number(pair[2]);
        box[0] = Math.min(box[0] ?? x, x);
        box[1] = Math.min(box[1] ?? y, y);
        box[2] = Math.max(box[2] ?? x, x);
        box[3] = Math.max(box[3] ?? y, y);
      }
    }
    if (!Number.isFinite(box[0])) {
      fly(k);
      return;
    }
    gl.fitBounds(
      [
        [box[0] ?? 0, box[1] ?? 0],
        [box[2] ?? 0, box[3] ?? 0],
      ],
      { padding: room(), duration: calm ? 0 : 900 },
    );
  }

  function letter(gl: Maplibre.Map) {
    isQueued = false;
    const held = detail.mid;
    const set = held != null && gl.getZoom() >= MID ? held.set : sheet;
    const finest = sheet.levels.findLast((level) => level.tiles != null);
    if (set.level === finest?.code && set.places.k.length > 500) {
      labels = [];
      return;
    }
    const box = gl.getBounds();
    const width = gl.getCanvas().clientWidth;
    const height = gl.getCanvas().clientHeight;
    const order: Array<number> = [];
    for (const [i, x] of set.places.x.entries()) {
      const y = set.places.y[i];
      if (x != null && y != null && box.contains([x, y])) {
        order.push(i);
      }
    }
    order.sort(
      (a, b) => (set.places.valid[b] ?? 0) - (set.places.valid[a] ?? 0),
    );
    const taken: Array<[number, number, number, number]> = [];
    const next: Array<{ k: number; name: string; x: number; y: number }> = [];
    for (const i of order) {
      const name = set.places.name[i] ?? "";
      const at = gl.project([set.places.x[i] ?? 0, set.places.y[i] ?? 0]);
      const half = name.length * 3.4 + 4;
      const edge: [number, number, number, number] = [
        at.x - half,
        at.y - 9,
        at.x + half,
        at.y + 9,
      ];
      if (
        name === "" ||
        edge[0] < 4 ||
        edge[1] < 4 ||
        edge[2] > width - 4 ||
        edge[3] > height - 4 ||
        taken.some(
          (other) =>
            other[0] < edge[2] &&
            edge[0] < other[2] &&
            other[1] < edge[3] &&
            edge[1] < other[3],
        )
      ) {
        continue;
      }
      taken.push(edge);
      next.push({ k: set.places.k[i] ?? 0, name, x: at.x, y: at.y });
      if (next.length >= 40) {
        break;
      }
    }
    labels = next;
  }

  const mount = (host: HTMLElement) => {
    const stop = new AbortController();
    let built: Maplibre.Map | null = null;
    void (async () => {
      const [maplibre, { Protocol }, worker] = await Promise.all([
        import("maplibre-gl"),
        import("pmtiles"),
        import("maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url"),
        import("maplibre-gl/dist/maplibre-gl.css"),
      ]);
      if (stop.signal.aborted) {
        return;
      }
      maplibre.setWorkerUrl(worker.default);
      maplibre.addProtocol("pmtiles", new Protocol().tile);
      const reader = probe(host);
      const gl = new maplibre.Map({
        container: host,
        style: {
          version: 8,
          projection: { type: "globe" },
          sources: {},
          layers: [
            {
              id: "paper",
              type: "background",
              paint: { "background-color": reader.color("var(--card)") },
            },
            ...["anchor-fills", "anchor-edges", "anchor-focus"].map((id) => ({
              id,
              type: "background" as const,
              layout: { visibility: "none" as const },
            })),
          ],
        },
        renderWorldCopies: false,
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        maxZoom: 16,
      });
      reader.done();
      built = gl;
      gl.touchZoomRotate.disableRotation();
      gl.once("style.load", () => {
        frame(
          gl,
          untrack(() => sheet),
          false,
        );
        build(
          gl,
          untrack(() => sheet),
        );
        map = gl;
      });
      gl.on("moveend", deepen);
      gl.on("move", () => {
        if (!isQueued) {
          isQueued = true;
          requestAnimationFrame(() => letter(gl));
        }
      });
      gl.on("mousemove", (event) => {
        const fills = groups
          .toSorted((a, b) => b.rank - a.rank)
          .map((group) => group.first)
          .filter((id) => gl.getLayer(id) != null);
        let k: number | null = null;
        for (const feature of gl.queryRenderedFeatures(event.point, {
          layers: fills,
        })) {
          if (typeof feature.id === "number" && lookup[feature.id] != null) {
            k = feature.id;
            break;
          }
        }
        hovered = k;
        pointer = { x: event.point.x, y: event.point.y };
        gl.getCanvas().style.cursor = k == null ? "" : "pointer";
      });
      gl.on("mouseout", () => {
        hovered = null;
        pointer = null;
      });
      gl.on("click", (event) => {
        const fills = groups
          .toSorted((a, b) => b.rank - a.rank)
          .map((group) => group.first)
          .filter((id) => gl.getLayer(id) != null);
        let k: number | null = null;
        for (const feature of gl.queryRenderedFeatures(event.point, {
          layers: fills,
        })) {
          if (typeof feature.id === "number" && lookup[feature.id] != null) {
            k = feature.id;
            break;
          }
        }
        if (k == null || pinned === k) {
          pinned = null;
          return;
        }
        pinned = k;
        hovered = k;
        if (isParts && lookup[k]?.set === sheet && gl.getZoom() < MID) {
          zoomTo(k);
        }
      });
    })();
    return () => {
      stop.abort();
      built?.remove();
      map = null;
    };
  };

  $effect(() => {
    const next = sheet;
    const gl = map;
    if (gl == null) {
      return;
    }
    untrack(() => {
      if (lookup[next.places.k[0] ?? -1]?.set === next) {
        return;
      }
      detail = {};
      build(gl, next);
      frame(gl, next, true);
    });
  });

  $effect(() => {
    const gl = map;
    if (gl != null) {
      letter(gl);
    }
  });

  let shade = $state(0);
  $effect(() => {
    const bump = () => requestAnimationFrame(() => (shade += 1));
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style", "data-theme"],
    });
    const offScheme = on(
      matchMedia("(prefers-color-scheme: dark)"),
      "change",
      bump,
    );
    return () => {
      observer.disconnect();
      offScheme();
    };
  });

  $effect(() => {
    const theme = mode.current;
    void shade;
    const gl = map;
    if (gl == null || theme == null) {
      return;
    }
    untrack(() => {
      const reader = probe(gl.getContainer());
      const paper = reader.color("var(--card)");
      gl.setPaintProperty("paper", "background-color", paper);
      const [r = 255, g = 255, b = 255] = (paper.match(/[\d.]+/g) ?? []).map(
        Number,
      );
      const stripes = new Uint8Array(12 * 12 * 4);
      for (let y = 0; y < 12; y += 1) {
        for (let x = 0; x < 12; x += 1) {
          if ((x + y) % 12 < 2) {
            stripes.set([r, g, b, 150], (y * 12 + x) * 4);
          }
        }
      }
      if (gl.hasImage("hatch")) {
        gl.removeImage("hatch");
      }
      gl.addImage(
        "hatch",
        { width: 12, height: 12, data: stripes },
        { pixelRatio: 2 },
      );
      for (const id of layers) {
        if (id.startsWith("seam:") || id.startsWith("edge:")) {
          gl.setPaintProperty(id, "line-color", paper);
        }
        if (id.startsWith("dots:")) {
          gl.setPaintProperty(id, "circle-stroke-color", paper);
        }
        if (id.startsWith("focus:") || id === "scope") {
          gl.setPaintProperty(
            id,
            "line-color",
            reader.color("var(--foreground)"),
          );
        }
      }
      if (gl.getLayer("context") != null) {
        gl.setPaintProperty(
          "context",
          "fill-color",
          reader.color(
            "color-mix(in oklab, var(--muted-foreground) 9%, var(--card))",
          ),
        );
        gl.setPaintProperty("context-seam", "line-color", paper);
      }
      reader.done();
      painted = {};
    });
  });

  $effect(() => {
    const mark = JSON.stringify([
      shown,
      whom?.key,
      range,
      only,
      isFlippedOnly,
      mode.current,
      shade,
      first?.key,
      $state.snapshot(palette),
    ]);
    const gl = map;
    const tiers = detail;
    const base = sheet;
    if (gl == null) {
      return;
    }
    untrack(() => {
      const level = base.levels.find((entry) => entry.code === base.level);
      if (level?.tiles != null) {
        paint(gl, base, `t:${level.tiles}`, mark);
      }
      for (const held of Object.values(tiers)) {
        paint(gl, held.set, held.source, mark);
      }
    });
  });

  $effect(() => {
    const gl = map;
    const hide = needsBefore;
    if (gl == null) {
      return;
    }
    for (const held of Object.values(detail)) {
      if (
        held.rank === 2 &&
        held.set.places.before.every((column) =>
          column.every((value) => value == null),
        )
      ) {
        for (const kind of ["fill", "hatch", "seam", "edge", "focus"]) {
          if (gl.getLayer(`${kind}:${held.source}`) != null) {
            gl.setLayoutProperty(
              `${kind}:${held.source}`,
              "visibility",
              hide ? "none" : "visible",
            );
          }
        }
      }
    }
    if (!hide) {
      untrack(deepen);
    }
  });

  $effect(() => {
    const gl = map;
    const strong = focus;
    const light = pinned != null && hovered !== pinned ? hovered : null;
    void seen;
    if (gl == null) {
      return;
    }
    untrack(() => {
      for (const mark of marked) {
        if (gl.getSource(mark.source) != null) {
          gl.setFeatureState(
            { source: mark.source, sourceLayer: "areas", id: mark.k },
            { focus: false, hover: false },
          );
        }
      }
      marked = [];
      for (const [k, state] of [
        [strong, { focus: true }],
        [light, { hover: true }],
      ] as const) {
        const hit = k == null ? null : lookup[k];
        if (k != null && hit != null) {
          gl.setFeatureState(
            { source: hit.source, sourceLayer: "areas", id: k },
            state,
          );
          marked.push({ source: hit.source, k });
        }
      }
    });
  });

  $effect(() =>
    on(window, "popstate", () => {
      href = location.href;
    }),
  );

  afterNavigate(() => {
    href = location.href;
  });

  $effect(() =>
    on(window, "keydown", (event) => {
      const target = event.target as HTMLElement | null;
      const isTyping =
        target?.closest("input, textarea, [contenteditable]") != null;
      if (event.key === "/" && !isTyping) {
        event.preventDefault();
        search?.focus();
      }
      if (event.key === "Escape") {
        if (isTyping) {
          term = "";
          search?.blur();
        } else {
          pinned = null;
        }
      }
    }),
  );
</script>

<svelte:head>
  <title
    >{m.atlas_page_title({
      title: sheet.title,
      place: sheet.scope.name,
    })}</title
  >
</svelte:head>

{#snippet finder(isWide: boolean)}
  <Command.Root
    shouldFilter={false}
    class={cn("relative overflow-visible bg-transparent", {
      "inline-[min(18rem,32cqi)] max-md:hidden": isWide,
      "md:hidden": !isWide,
    })}
  >
    <Command.Input
      bind:ref={
        () => (isWide ? search : null),
        (node: HTMLInputElement | null) => {
          if (isWide) {
            search = node;
          }
        }
      }
      bind:value={term}
      placeholder={m.atlas_search()}
      aria-label={m.atlas_search()}
    />
    {#if term.trim().length >= 2}
      <Command.List
        class="absolute inset-x-0 inset-bs-full z-20 mbs-1 rounded-sm bg-popover p-1 shadow-menu ring-1 ring-border"
      >
        <Command.Empty>{m.atlas_search_empty()}</Command.Empty>
        {#each found as row (row.k)}
          <Command.Item
            value={String(row.k)}
            onSelect={() => {
              term = "";
              search?.blur();
              fly(row.k);
            }}
          >
            <span class="flex-1 truncate">{row.name}</span>
            {#if row.where !== ""}
              <span class="shrink-0 text-xs text-muted-foreground"
                >{row.where}</span
              >
            {/if}
          </Command.Item>
        {/each}
      </Command.List>
    {/if}
  </Command.Root>
{/snippet}

{#snippet comparer(isWide: boolean)}
  {#if sheet.targets.length > 0}
    <Select.Root
      type="single"
      value={sheet.target ?? "none"}
      onValueChange={(value: string) => {
        const [where = "", level = ""] = asked.split("|");
        move(
          where,
          level === "" ? null : level,
          value === "none" ? null : value,
        );
      }}
    >
      <Select.Trigger
        class={cn("shrink-0", {
          "max-sm:hidden": isWide,
          "inline-full sm:hidden": !isWide,
        })}
        aria-label={m.atlas_compare()}
      >
        {sheet.target == null
          ? m.atlas_compare_none()
          : m.atlas_compare_with({ year: year ?? "" })}
      </Select.Trigger>
      <Select.Content align="end">
        <Select.Item value="none" label={m.atlas_compare_none()} />
        {#each sheet.targets as target (target.id)}
          <Select.Item value={target.id} label={target.label} />
        {/each}
      </Select.Content>
    </Select.Root>
  {/if}
{/snippet}

{#snippet ledger(hit: { set: Sheet; i: number })}
  {const reading = $derived(read(hit.set, hit.i))}
  {const rows = $derived(
    hit.set.legend
      .map((item, index) => ({
        item,
        index,
        share: (hit.set.places.shares[index]?.[hit.i] ?? 0) / 10_000,
        then: hit.set.places.before[index]?.[hit.i] ?? null,
      }))
      .filter((row) => row.share > 0 || (row.then ?? 0) > 0)
      .toSorted((a, b) => b.share - a.share)
      .slice(0, 5),
  )}
  {const part = $derived(
    hit.set === sheet
      ? sheet.parts.find((entry) => entry.k === hit.set.places.k[hit.i])
      : undefined,
  )}
  {const lead = $derived(
    reading.leader == null ? null : hit.set.legend[reading.leader],
  )}
  <div class="grid gap-3">
    <div class="grid gap-0.5">
      <div class="flex items-start justify-between gap-3">
        <h2 class="text-lg/6 font-[720] text-balance">
          {hit.set.places.name[hit.i]}
        </h2>
        {#if pinned != null}
          <button
            type="button"
            class="-me-1.5 -mbs-0.5 grid shrink-0 place-items-center rounded-xs text-muted-foreground block-7 inline-7 hover:bg-secondary hover:text-foreground"
            aria-label={m.atlas_unpin()}
            title={m.atlas_unpin()}
            onclick={() => (pinned = null)}
          >
            <XIcon class="block-4 inline-4" />
          </button>
        {/if}
      </div>
      <p
        class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
      >
        {[
          hit.set === sheet ? null : hit.set.scope.name,
          m.atlas_votes({ votes: count(hit.set.places.valid[hit.i] ?? 0) }),
        ]
          .filter((part) => part != null)
          .join(", ")}
      </p>
    </div>
    {#if lead != null}
      <p
        class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[0.9375rem]"
      >
        <span class="font-semibold">
          {m.atlas_lead({
            name: lead.label,
            points: (reading.lead * 100).toFixed(1),
          })}
        </span>
        {#if reading.isFlip && isCompared && year != null}
          <span
            class="rounded-xs px-1.5 text-xs/5 font-semibold ring-1 ring-foreground"
            >{m.atlas_flipped({ year })}</span
          >
          {#if hit.set.estimated}
            <span class="text-xs text-muted-foreground"
              >{m.atlas_estimated()}</span
            >
          {/if}
        {/if}
      </p>
    {/if}
    <ul class="grid gap-2">
      {#each rows as row (row.item.key)}
        <li class="grid gap-1">
          <div class="flex items-baseline gap-2 text-[0.8125rem]">
            <span
              class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
              style:background-color={hue(hit.set, row.index)}
            ></span>
            <span class="flex-1 truncate">{row.item.label}</span>
            <span class="font-figures font-semibold tabular-nums"
              >{percent(row.share)}</span
            >
            {#if row.then != null}
              <span
                class="text-end font-figures text-xs text-muted-foreground tabular-nums inline-11"
                >{points(row.share - row.then / 10_000)}</span
              >
            {/if}
          </div>
          <div class="relative rounded-xs bg-secondary block-1.5">
            <div
              class="absolute inset-y-0 inset-s-0 rounded-xs"
              style:inline-size="{row.share * 100}%"
              style:background-color={hue(hit.set, row.index)}
            ></div>
            {#if row.then != null}
              <div
                class="absolute -inset-y-0.5 bg-foreground inline-0.5"
                style:inset-inline-start="calc({row.then / 100}% - 1px)"
              ></div>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
    {#if reading.isUnmatched && year != null}
      {const overlap = $derived(hit.set.places.redrawn[hit.i] ?? null)}
      <p class="text-[0.8125rem] text-pretty text-muted-foreground">
        {overlap == null
          ? m.map_unmatched_missing_line({ year })
          : m.map_unmatched_redrawn_line({ year, percent: percent(overlap) })}
      </p>
    {/if}
    {#if isCompared && year != null && reading.swing != null}
      {const side = $derived(reading.swing >= 0 ? first : second)}
      <p class="text-[0.8125rem] text-muted-foreground">
        {m.atlas_shift({
          name: side?.label ?? "",
          points: Math.abs(reading.swing * 100).toFixed(1),
          year,
        })}
        {#if hit.set.estimated}
          <span class="font-semibold text-foreground"
            >({m.atlas_estimated()})</span
          >
        {/if}
        <span class="block">{m.atlas_ticks({ year })}</span>
      </p>
    {/if}
    {#if part != null}
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="flex items-center rounded-sm px-2.5 text-[0.8125rem] font-semibold ring-1 ring-input block-8 hover:bg-secondary"
          onclick={() => zoomTo(part.k)}>{m.atlas_zoom_to()}</button
        >
        <button
          type="button"
          class="flex items-center rounded-sm px-2.5 text-[0.8125rem] font-semibold ring-1 ring-input block-8 hover:bg-secondary"
          onclick={() => scope(part.unit)}
          >{m.atlas_alone({ place: part.name })}</button
        >
      </div>
    {/if}
  </div>
{/snippet}

<div
  class="@container fixed inset-0 z-40 grid grid-rows-[auto_minmax(0,1fr)] bg-card"
>
  <header
    class="relative z-10 flex items-center gap-x-3 border-be bg-card px-3 block-14 sm:gap-x-4 sm:px-4"
  >
    <a
      href={resolve("/")}
      class="shrink-0"
      aria-label={m.wordmark()}
      title={m.wordmark()}><Monogram size={26} /></a
    >
    <div class="grid flex-1 min-inline-0">
      <h1 class="truncate text-[0.9375rem]/5 font-[720]">{sheet.title}</h1>
      <nav aria-label={m.atlas_trail()}>
        <ol
          class="flex items-center gap-1 text-[0.8125rem]/5 text-muted-foreground"
        >
          {#each sheet.trail as crumb, index (crumb.unit)}
            <li class="flex items-center gap-1 min-inline-0">
              {#if index > 0}
                <span aria-hidden="true">/</span>
              {/if}
              {#if index === sheet.trail.length - 1}
                <span
                  class="truncate font-semibold text-foreground"
                  aria-current="page">{crumb.name}</span
                >
              {:else}
                <a
                  href={resolve("/map/[...path]", {
                    path: [data.path, crumb.unit]
                      .filter((part) => part !== "")
                      .join("/"),
                  })}
                  class="truncate underline decoration-border underline-offset-3 hover:text-foreground hover:decoration-foreground"
                  onclick={(event) => {
                    event.preventDefault();
                    scope(crumb.unit);
                  }}>{crumb.name}</a
                >
              {/if}
            </li>
          {/each}
        </ol>
      </nav>
    </div>
    {@render finder(true)}
    {@render comparer(true)}
    <a
      href={resolve("/[...path]", { path: sheet.scope.path ?? data.path })}
      class="flex shrink-0 items-center rounded-sm px-2.5 text-[0.8125rem] font-semibold ring-1 ring-input block-8 hover:bg-secondary max-sm:hidden"
      >{m.atlas_results()}</a
    >
    <a
      href={resolve("/[...path]", { path: sheet.scope.path ?? data.path })}
      class="grid shrink-0 place-items-center rounded-sm block-8 inline-8 hover:bg-secondary"
      aria-label={m.atlas_close()}
      title={m.atlas_close()}><XIcon class="block-4.5 inline-4.5" /></a
    >
  </header>

  <div class="relative min-block-0">
    <div
      class="absolute inset-0"
      role="application"
      aria-roledescription="map"
      aria-label={m.map_label({ noun: current?.noun ?? "" })}
      aria-describedby="{uid}-hint"
    >
      <div class="block-full inline-full" {@attach mount}></div>
    </div>
    <div
      class="pointer-events-none absolute inset-0 overflow-hidden"
      aria-hidden="true"
    >
      {#each labels as label (label.k)}
        <span
          class="absolute inset-s-0 inset-bs-0 text-[0.75rem] font-semibold whitespace-nowrap text-foreground [-webkit-text-stroke:3px_var(--card)] [paint-order:stroke_fill]"
          style:transform="translate({label.x}px, {label.y}px) translate(-50%,
          -50%)">{label.name}</span
        >
      {/each}
    </div>
    <p id="{uid}-hint" class="sr-only">{m.atlas_hint()}</p>

    {#if isLoading || pending > 0}
      <p
        class="pointer-events-none absolute inset-x-0 inset-bs-3 z-10 mx-auto flex items-center gap-2 rounded-sm bg-card/95 px-3 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border block-8 inline-fit"
        role="status"
        transition:fade={{ duration: calm ? 0 : 120 }}
      >
        <span
          class="animate-spin rounded-full border-2 border-border border-bs-foreground block-3.5 inline-3.5 motion-reduce:animate-none"
          aria-hidden="true"
        ></span>
        {m.atlas_loading()}
      </p>
    {/if}

    <section
      class={cn(
        "absolute inset-s-3 inset-be-3 z-10 grid gap-3 rounded-sm bg-card/96 p-3.5 shadow-menu ring-1 ring-border inline-[min(19.5rem,calc(100%-1.5rem))] max-sm:overflow-y-auto max-sm:max-block-[60%]",
        { "max-sm:hidden": !isKeyOpen },
      )}
      aria-labelledby="{uid}-key"
    >
      <h2 id="{uid}-key" class="sr-only">{m.atlas_key()}</h2>
      {@render finder(false)}
      {@render comparer(false)}
      <div class="grid gap-1.5">
        <span class="text-xs font-semibold text-muted-foreground"
          >{m.map_color_by()}</span
        >
        <div class="flex flex-wrap gap-1.5">
          {#each [{ value: "winner" as const, label: m.map_mode_winner(), key: null }, { value: "votes" as const, label: m.map_mode_bubbles(), key: null }, ...sheet.legend
              .slice(0, 3)
              .map( (item) => ({ value: "share" as const, label: item.label, key: item.key }) ), ...(isCompared ? [{ value: "swing" as const, label: m.map_mode_swing(), key: null }, { value: "flips" as const, label: m.map_mode_flips(), key: null }] : [])] as choice (`${choice.value}:${choice.key ?? ""}`)}
            {const isOn = $derived(
              shown === choice.value &&
                (choice.key == null || whom?.key === choice.key),
            )}
            <button
              type="button"
              class={cn(
                "flex items-center gap-1.5 rounded-sm px-2 text-[0.8125rem] font-semibold whitespace-nowrap ring-1 ring-input block-7 hover:bg-secondary",
                {
                  "bg-primary text-primary-foreground ring-primary hover:bg-primary":
                    isOn,
                },
              )}
              aria-pressed={isOn}
              onclick={() => {
                view = choice.value;
                who = choice.key;
                range = null;
              }}
            >
              {#if choice.key != null}
                <span
                  class="rounded-xs block-2 inline-2"
                  style:background-color={tones[choice.key]}
                ></span>
              {/if}
              {choice.label}
            </button>
          {/each}
        </div>
      </div>
      {#if bars.bars.some((bar) => bar.count > 0)}
        {const hit = $derived(entry)}
        {const marker = $derived(
          hit?.set !== sheet ? null : read(hit.set, hit.i).value,
        )}
        <Histogram
          bars={bars.bars}
          domain={scale.domain}
          step={scale.step}
          marker={marker == null
            ? null
            : Math.min(scale.domain[1], Math.max(scale.domain[0], marker))}
          {ticks}
          label={m.atlas_range()}
          bind:range
        />
      {/if}
      {#if sheet.target != null && !isCompared}
        <p class="text-[0.8125rem] text-muted-foreground">
          {m.atlas_no_comparison()}
        </p>
      {/if}
      {#if needsBefore && year != null && unmatched.redrawn + unmatched.missing > 0}
        <p class="flex flex-wrap items-center gap-x-1.5 text-[0.8125rem]">
          <span
            class="rounded-xs bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1px,transparent_1px_4px)] ring-1 ring-border block-3 inline-4"
          ></span>
          <span class="font-semibold">{m.map_unmatched_key()}</span>
          <span class="font-figures text-xs text-muted-foreground tabular-nums"
            >{[
              unmatched.redrawn > 0
                ? m.map_unmatched_redrawn({
                    count: count(unmatched.redrawn),
                    year,
                  })
                : null,
              unmatched.missing > 0
                ? m.map_unmatched_missing({
                    count: count(unmatched.missing),
                    year,
                  })
                : null,
            ]
              .filter((part) => part != null)
              .join(", ")}</span
          >
        </p>
      {/if}
      {#if isEstimated && year != null}
        <p
          class="border-s-2 border-foreground ps-2 text-[0.8125rem] text-muted-foreground"
        >
          {m.atlas_estimated_note({ year })}
        </p>
      {/if}
      <div class="flex flex-wrap gap-1">
        {#each leads.slice(0, 6) as lead (lead.index)}
          {const item = $derived(sheet.legend[lead.index])}
          {#if item != null}
            <button
              type="button"
              class={cn(
                "flex items-center gap-1.5 rounded-xs px-1.5 text-[0.8125rem] block-7 hover:bg-secondary",
                { "marked bg-secondary": only === item.key },
                { "opacity-45": only != null && only !== item.key },
              )}
              style:--party={tones[item.key]}
              aria-pressed={only === item.key}
              title={m.atlas_only({ name: item.label })}
              onclick={() => (only = only === item.key ? null : item.key)}
            >
              <span class="rounded-xs bg-(--party) block-2.5 inline-2.5"></span>
              <span class="font-semibold">{item.label}</span>
              <span
                class="font-figures text-xs text-muted-foreground tabular-nums"
                >{count(lead.won)}</span
              >
            </button>
          {/if}
        {/each}
      </div>
      <div
        class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-bs pbs-2.5"
      >
        <span
          class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
        >
          {m.atlas_shown({
            shown: count(bars.admitted),
            total: count(bars.measured),
          })}
        </span>
        {#if isCompared}
          <label class="flex items-center gap-2 text-[0.8125rem] font-semibold">
            <Switch
              checked={isFlippedOnly}
              onCheckedChange={(value: boolean) => (isFlippedOnly = value)}
            />
            {m.atlas_flipped_only()}
          </label>
        {/if}
        {#if range != null || only != null || isFlippedOnly}
          <button
            type="button"
            class="text-[0.8125rem] font-semibold underline decoration-border underline-offset-3 hover:decoration-foreground"
            onclick={() => {
              range = null;
              only = null;
              isFlippedOnly = false;
            }}>{m.atlas_clear()}</button
          >
        {/if}
      </div>
      {#if sheet.levels.length > 1 && !isParts}
        <div class="grid gap-1.5">
          <span class="text-xs font-semibold text-muted-foreground"
            >{m.map_level()}</span
          >
          <div class="flex">
            {#each sheet.levels as level (level.code)}
              <button
                type="button"
                class={cn(
                  "-ms-px flex items-center gap-1.5 border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                  {
                    "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                      level.code === sheet.level,
                  },
                )}
                aria-pressed={level.code === sheet.level}
                onclick={() => {
                  const [where = "", , vs = ""] = asked.split("|");
                  move(where, level.code, vs === "" ? null : vs);
                }}
                >{level.noun.charAt(0).toUpperCase() + level.noun.slice(1)}
                <span
                  class="font-figures text-[0.6875rem] font-normal tabular-nums"
                  >{count(level.count)}</span
                ></button
              >
            {/each}
          </div>
        </div>
      {:else if isParts}
        <p class="text-xs text-muted-foreground">{m.atlas_zoom_hint()}</p>
      {/if}
      {#if sheet.isAllied || grouped}
        <div class="grid gap-1.5">
          <span class="text-xs font-semibold text-muted-foreground"
            >{m.group_label()}</span
          >
          <div class="flex">
            {#each [{ isAlliance: false, label: m.group_option() }, { isAlliance: true, label: m.group_alliance() }] as choice (choice.label)}
              <button
                type="button"
                class={cn(
                  "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                  {
                    "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                      choice.isAlliance === grouped,
                  },
                )}
                aria-pressed={choice.isAlliance === grouped}
                onclick={() => {
                  const [where = "", level = "", vs = ""] = asked.split("|");
                  move(
                    where,
                    level === "" ? null : level,
                    vs === "" ? null : vs,
                    choice.isAlliance ? "alliance" : null,
                  );
                }}>{choice.label}</button
              >
            {/each}
          </div>
        </div>
      {/if}
    </section>

    <button
      type="button"
      class={cn(
        "absolute inset-s-3 inset-be-3 z-10 flex items-center gap-2 rounded-sm bg-card px-3 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border block-10 sm:hidden",
        { hidden: isKeyOpen || pinned != null },
      )}
      onclick={() => (isKeyOpen = true)}
    >
      <SlidersHorizontalIcon class="block-4 inline-4" />
      {m.atlas_key()}
    </button>
    {#if isKeyOpen}
      <button
        type="button"
        class="absolute inset-e-3 inset-be-3 z-20 grid place-items-center rounded-sm bg-card shadow-menu ring-1 ring-border block-10 inline-10 sm:hidden"
        aria-label={m.atlas_key_close()}
        onclick={() => (isKeyOpen = false)}
        ><XIcon class="block-4 inline-4" /></button
      >
    {/if}

    {#if entry != null}
      <aside
        class={cn(
          "absolute inset-e-3 inset-bs-3 z-10 rounded-sm bg-card/96 p-3.5 shadow-menu ring-1 ring-border inline-[min(19.5rem,calc(100%-1.5rem))] max-sm:inset-bs-auto max-sm:inset-be-3 max-sm:overflow-y-auto max-sm:max-block-[55%]",
          { "max-sm:hidden": pinned == null || isKeyOpen },
        )}
        aria-live="polite"
      >
        {@render ledger(entry)}
      </aside>
    {:else}
      <p
        class="pointer-events-none absolute inset-e-3 inset-bs-3 z-10 rounded-sm bg-card/90 px-3 text-[13px]/8 text-muted-foreground ring-1 ring-border block-8 max-sm:hidden"
      >
        {m.atlas_pin_hint()}
      </p>
    {/if}

    {#if glance != null && pointer != null}
      <div
        class="pointer-events-none absolute z-20 grid gap-0.5 rounded-sm bg-card px-2.5 py-1.5 text-[0.8125rem] shadow-menu ring-1 ring-border max-inline-64 max-sm:hidden"
        style:inset-inline-start="{pointer.x + 14}px"
        style:translate={pointer.x >
        (map?.getContainer().clientWidth ?? 0) - 360
          ? "calc(-100% - 28px) 0"
          : undefined}
        style:inset-block-start="{pointer.y + 14}px"
      >
        <span class="truncate font-semibold">{glance.name}</span>
        {#if glance.leader != null}
          <span class="flex items-center gap-1.5">
            <span
              class="rounded-xs block-2 inline-2"
              style:background-color={glance.tone}
            ></span>
            {m.atlas_lead({
              name: glance.leader.label,
              points: (glance.lead * 100).toFixed(1),
            })}
          </span>
        {/if}
        <span class="font-figures text-xs text-muted-foreground tabular-nums">
          {m.atlas_versus({
            name: glance.side?.label ?? "",
            points: Math.abs(glance.gap * 100).toFixed(1),
            place: glance.against,
          })}
        </span>
      </div>
    {/if}

    <div
      class="absolute inset-e-3 inset-be-3 z-10 flex flex-col overflow-hidden rounded-sm bg-card shadow-menu ring-1 ring-border max-sm:hidden"
    >
      <button
        type="button"
        class="grid place-items-center block-8 inline-8 hover:bg-secondary"
        aria-label={m.map_zoom_in()}
        title={m.map_zoom_in()}
        onclick={() => map?.zoomIn()}
        ><PlusIcon class="block-4 inline-4" /></button
      >
      <button
        type="button"
        class="grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary"
        aria-label={m.map_zoom_out()}
        title={m.map_zoom_out()}
        onclick={() => map?.zoomOut()}
        ><MinusIcon class="block-4 inline-4" /></button
      >
      <button
        type="button"
        class="grid place-items-center border-bs border-border block-8 inline-8 hover:bg-secondary"
        aria-label={m.map_reset()}
        title={m.map_reset()}
        onclick={() => {
          if (map != null) {
            frame(map, sheet, true);
          }
        }}><ScanIcon class="block-4 inline-4" /></button
      >
    </div>
    {#if credits.length > 0}
      <p
        class="pointer-events-none absolute inset-e-14 inset-be-1 z-0 truncate text-[0.625rem] text-muted-foreground max-inline-[40%] max-sm:hidden"
      >
        {const vintage = $derived(
          /\/(\d{4}-\d{2}-\d{2})$/u.exec(
            sheet.levels.find((entry) => entry.code === sheet.level)?.tiles ??
              "",
          )?.[1],
        )}
        {vintage == null
          ? m.map_boundaries({ publishers: credits.join("; ") })
          : m.map_boundaries_dated({
              publishers: credits.join("; "),
              date: date(vintage),
            })}
      </p>
    {/if}
  </div>
</div>
