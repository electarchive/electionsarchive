<script lang="ts">
  import { afterNavigate, goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import BoxSelectIcon from "@lucide/svelte/icons/box-select";
  import KeyboardIcon from "@lucide/svelte/icons/keyboard";
  import MapIcon from "@lucide/svelte/icons/map";
  import MinusIcon from "@lucide/svelte/icons/minus";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import ScanIcon from "@lucide/svelte/icons/scan";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SheetIcon from "@lucide/svelte/icons/sheet";
  import XIcon from "@lucide/svelte/icons/x";
  import { createQuery } from "@tanstack/svelte-query";
  import { untrack } from "svelte";
  import { MediaQuery } from "svelte/reactivity";
  import { fade } from "svelte/transition";
  import type { Lens } from "#lib/atlas/color.js";
  import type { Electors, Mosaic, Sheet, Shell } from "#lib/atlas/data.js";
  import type { Hit } from "#lib/atlas/stage.svelte.js";
  import type { Tier } from "#lib/atlas/tiers.js";
  import Analysis from "#lib/atlas/analysis.svelte";
  import { admits, hue, read, share, was } from "#lib/atlas/color.js";
  import { layered, load, loadMosaic } from "#lib/atlas/data.js";
  import Drawer from "#lib/atlas/drawer.svelte";
  import { tally } from "#lib/atlas/electors.js";
  import Electoral from "#lib/atlas/electors.svelte";
  import Hover from "#lib/atlas/hover.svelte";
  import Insets from "#lib/atlas/insets.svelte";
  import Key from "#lib/atlas/key.svelte";
  import Outlying from "#lib/atlas/outlying.svelte";
  import Places from "#lib/atlas/places.svelte";
  import Rail from "#lib/atlas/rail.svelte";
  import Result from "#lib/atlas/result.svelte";
  import { Stage } from "#lib/atlas/stage.svelte.js";
  import { View } from "#lib/atlas/state.svelte.js";
  import Switcher from "#lib/atlas/switcher.svelte";
  import {
    active,
    chain,
    choices,
    fine,
    isParted,
    starts,
  } from "#lib/atlas/tiers.js";
  import Monogram from "#lib/components/monogram.svelte";
  import Standing from "#lib/components/standing.svelte";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as Dialog from "#lib/components/ui/dialog/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { count, date, plural } from "#lib/format.js";
  import { basemap } from "#lib/map/basemap.js";
  import { Engine, proportional, reach } from "#lib/map/engine.svelte.js";
  import Gaps from "#lib/map/gaps.svelte";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  type Point = { x: number; y: number };

  const {
    contest,
    path,
    whole,
    shell,
    electors = null,
    scheme,
    query,
  }: {
    contest: string;
    path: string;
    whole: string | null;
    shell: Shell;
    electors?: Electors | null;
    scheme: string;
    query: string;
  } = $props();

  const uid = $props.id();
  const wide = new MediaQuery("min-width: 48rem", false);
  const tall = new MediaQuery("min-height: 40rem", true);
  const calm = new MediaQuery("prefers-reduced-motion: reduce", false);
  const initial = new URLSearchParams(untrack(() => query));
  const view = new View(initial);

  const unit = $derived(
    decodeURI((page.shallow?.url ?? page.url).pathname).slice(
      `/map/${path}`.length + 1,
    ),
  );
  const ask = $derived({
    unit: unit === "" ? null : unit,
    vs: view.vs,
    group: view.group,
    whole,
    round: view.round,
  });

  const SHARP = 8;

  let base = $state.raw<Sheet | null>(null);
  let mosaic = $state.raw<Mosaic | null>(null);
  let isSettled = $state(false);
  let extras = $state.raw<Record<string, Sheet>>({});
  let parts = $state.raw<Record<string, Sheet>>({});
  let pending = $state(0);
  let isFailed = $state(false);
  let begins = $state.raw<Array<number>>([]);
  let zoom = $state(0);
  let fit = $state(0);
  let tier = $state(0);
  let moved = $state(0);
  let isAnalyzing = $state(false);
  let hovered = $state.raw<Hit | null>(null);
  let pointer = $state<{ x: number; y: number } | null>(null);
  const room = $state({ width: 0, height: 0 });
  let drawn = $state(0);
  let detent = $state<"peek" | "half" | "full">("peek");
  let named = $state(0);
  let camera = $state<string | null>(initial.get("at"));
  let isAdding = $state(false);
  let isArrived = $state(false);
  let isShift = false;
  let isApart = $state(false);
  let above = $state.raw<Sheet | null>(null);
  let lasso = $state.raw<{
    isBox: boolean;
    id: number;
    left: number;
    top: number;
    points: Array<Point>;
  } | null>(null);
  let term = $state("");
  let isSearching = $state(false);
  let search = $state<HTMLInputElement | null>(null);
  let wanted = $state("");
  let chase = $state<number | null>(null);
  let isSwitching = $state(false);
  let isHelping = $state(false);
  let switcher = $state<{
    move?: (delta: -1 | 1) => void;
    [key: string]: unknown;
  } | null>(null);
  let built = "";
  let isQueued = false;
  let baseAsk = "";
  const asked = new Set<string>();
  const fetched = new Set<string>();

  const head = $derived<Shell | Sheet>(base ?? shell);
  const isNational = $derived(base?.apart != null && zoom < fit + 1.5);
  const aside = $derived.by(() => {
    void stage.version;
    void drawn;
    void tier;
    return isNational ? stage.aside(tier) : [];
  });
  const outlying = $derived.by(() => {
    void stage.version;
    void drawn;
    void tier;
    return base?.apart != null ? stage.outlying(tier) : [];
  });
  const levels = $derived(chain(head));
  const sample = $derived(Object.values(parts)[0] ?? null);
  const finest = $derived(isSettled ? fine(head, mosaic, sample) : null);
  const all = $derived(choices(head, mosaic, sample));
  const locked = $derived(
    view.level == null
      ? null
      : (all.find((choice) => choice.code === view.level) ?? null),
  );
  const tiers = $derived<Array<Tier>>(
    locked != null ? [locked] : finest == null ? levels : [...levels, finest],
  );
  const now = $derived(tiers[tier] ?? tiers[0] ?? null);

  const tones = $derived.by(() => {
    void $state.snapshot(palette);
    const found = new Map<string, string>();
    for (const set of [base, mosaic?.set]) {
      for (const item of set?.legend ?? []) {
        found.set(item.key, swatch(item.color, item.key));
      }
    }
    return found;
  });
  const isCompared = $derived(
    base != null &&
      base.target != null &&
      (base.places.redrawn.some((value) => value != null) ||
        base.places.before.some((column) =>
          column.some((value) => value != null),
        )),
  );
  const isProportional = $derived(
    base != null &&
      proportional(
        base.places.leader.flatMap((leader, i) =>
          leader == null ? [] : [base?.places.held?.[i] ?? null],
        ),
      ),
  );
  const legend = $derived(
    (base?.legend ?? []).map((item) =>
      whole == null
        ? item
        : {
            ...item,
            label: item.short ?? item.label,
            then: item.short ?? item.then,
          },
    ),
  );
  const labels = $derived(
    new Map(legend.map((item) => [item.key, item.label])),
  );
  const pasts = $derived(
    new Map(legend.map((item) => [item.key, item.then ?? item.label])),
  );
  const baseline = $derived.by(() => {
    const [one, two] = base?.legend ?? [];
    if (one == null || two == null) {
      return 0;
    }
    const at = above?.places.k.indexOf(head.scope.k) ?? -1;
    const set = above != null && at !== -1 ? above : base;
    if (set == null) {
      return 0;
    }
    const column = (item: { key: string; label: string }) =>
      set.places.shares[
        set.legend.findIndex(
          (entry) => entry.key === item.key || entry.label === item.label,
        )
      ] ?? [];
    const ahead = column(one);
    const behind = column(two);
    let votes = 0;
    let spread = 0;
    for (const i of set === above ? [at] : set.places.k.keys()) {
      const valid = set.places.valid[i] ?? 0;
      votes += valid;
      spread += (((ahead[i] ?? 0) - (behind[i] ?? 0)) / 10_000) * valid;
    }
    return votes === 0 ? 0 : spread / votes;
  });
  const lens = $derived.by<Lens>(() => {
    const mode =
      (!isCompared && (view.mode === "swing" || view.mode === "flips")) ||
      (view.mode === "split" && !isProportional) ||
      (view.mode === "lean" && legend.length < 2)
        ? "winner"
        : view.mode;
    const first = legend[0]?.key ?? null;
    return {
      mode,
      first,
      second: legend[1]?.key ?? null,
      whom:
        mode === "share"
          ? (legend.find((item) => item.key === view.who)?.key ?? first)
          : null,
      isBanded: mode === "split" || (mode === "winner" && !isProportional),
      isCompared,
      baseline,
      range: view.range,
      only: view.only,
      isFlippedOnly: view.isFlippedOnly,
      tone: (key) =>
        (key == null ? null : tones.get(key)) ?? "var(--muted-foreground)",
    };
  });
  const year = $derived(
    base?.targets
      .find((target) => target.id === base?.target)
      ?.date.slice(0, 4) ?? null,
  );

  const engine = new Engine(
    untrack(() => shell.frame) ?? {
      west: -20,
      south: -20,
      east: 20,
      north: 20,
    },
    (width) =>
      width >= 768
        ? { top: 32, right: Math.max(48, width * 0.08), bottom: 32, left: 48 }
        : {
            top: 64,
            right: 60,
            bottom: Math.max(24, untrack(() => sheet) + 16),
            left: 16,
          },
    {
      onReady: () => {
        engine.gl?.boxZoom.disable();
        measure();
        const at = view.at;
        if (at != null) {
          engine.jump([at[0], at[1]], at[2]);
        }
        zoom = engine.gl?.getZoom() ?? 0;
      },
      onMove: () => {
        if (!isQueued) {
          isQueued = true;
          requestAnimationFrame(() => {
            isQueued = false;
            zoom = engine.gl?.getZoom() ?? zoom;
            tier = locked != null ? 0 : active(begins, zoom, tier);
          });
        }
      },
      onMoveEnd: () => {
        const gl = engine.gl;
        if (gl == null) {
          return;
        }
        const middle = gl.getCenter();
        camera = engine.isFramed
          ? null
          : `${middle.lng.toFixed(4)},${middle.lat.toFixed(4)},${gl.getZoom().toFixed(2)}`;
        moved += 1;
        stage.letter(tier);
        deepen();
      },
      onPointer: ({ kind, point, isTouch }) => {
        if (kind === "leave") {
          hover(null);
          pointer = null;
          return;
        }
        const hit = stage.pick(point, tier);
        if (kind === "move") {
          hover(hit);
          pointer = point;
          engine.cursor(hit != null);
          return;
        }
        if (isTouch) {
          hover(null);
        }
        choose(hit, isShift);
      },
    },
  );
  const stage = new Stage(engine);
  let sheet = $state(0);

  function measure() {
    const gl = engine.gl;
    const frame = head.frame;
    if (gl == null || frame == null) {
      return;
    }
    fit = engine.fitZoom(frame);
    const world = 512 * 2 ** fit;
    const mercator = (lat: number) =>
      Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) / (2 * Math.PI);
    const area =
      ((frame.east - frame.west) / 360) *
      world *
      (mercator(frame.north) - mercator(frame.south)) *
      world *
      0.5;
    const next = starts(tiers, fit, area);
    if (
      next.length !== begins.length ||
      next.some((value, index) => Math.abs(value - (begins[index] ?? 0)) > 0.2)
    ) {
      begins = next;
    }
  }

  function choose(hit: Hit | null, isMany: boolean) {
    const k = hit == null ? null : (hit.set.places.k[hit.i] ?? null);
    if (isAdding || isMany) {
      if (k != null) {
        view.picked = view.picked.includes(k)
          ? view.picked.filter((other) => other !== k)
          : [...view.picked, k];
      }
      return;
    }
    view.picked =
      k == null || (view.picked.length === 1 && view.picked[0] === k)
        ? []
        : [k];
  }

  function hover(hit: Hit | null) {
    if (hit === hovered) {
      return;
    }
    hovered = hit;
    stage.hover(hit);
    if (hit != null) {
      name(hit);
    }
  }

  function name(hit: Hit) {
    const held = mosaic;
    if (held == null || hit.set.places.name[hit.i] !== "") {
      return;
    }
    const sets = [held.set, ...layered(held)];
    const at = held.set.places.k.indexOf(hit.set.places.k[hit.i] ?? 0);
    if (!sets.includes(hit.set) || at < 0) {
      return;
    }
    const part = held.part[at] ?? -1;
    const layer = held.layer[at] ?? 0;
    const key = `${held.key}|${part}|${layer}`;
    if (asked.has(key)) {
      return;
    }
    asked.add(key);
    const ks: Array<number> = [];
    for (const [i, k] of held.set.places.k.entries()) {
      if (
        (held.part[i] ?? -1) === part &&
        (held.layer[i] ?? 0) === layer &&
        held.set.places.name[i] === ""
      ) {
        ks.push(k);
      }
    }
    orpc.maps.names
      .call({ id: contest, k: ks })
      .then((found) => {
        const names = new Map(ks.map((k, j) => [k, found[j] ?? ""]));
        for (const set of sets) {
          for (const [i, k] of set.places.k.entries()) {
            const text = names.get(k);
            if (text != null) {
              set.places.name[i] = text;
            }
          }
        }
        named += 1;
      })
      .catch(() => asked.delete(key));
  }

  function fetch(
    key: string,
    run: () => Promise<Sheet>,
    done: (set: Sheet) => void,
  ) {
    if (fetched.has(key)) {
      return;
    }
    fetched.add(key);
    pending += 1;
    void run()
      .then(done)
      .catch(() => fetched.delete(key))
      .finally(() => {
        pending -= 1;
      });
  }

  function deepen() {
    const from = base;
    const gl = engine.gl;
    const scope = JSON.stringify(ask);
    if (from == null || gl == null || baseAsk !== scope) {
      return;
    }
    for (const [index, entry] of tiers.entries()) {
      const start = begins[index] ?? -Infinity;
      const isNear = locked != null || zoom >= start - 1.2;
      if (!isNear || entry.kind !== "level" || entry.code === from.level) {
        continue;
      }
      fetch(
        `${scope}|${entry.code}`,
        async () => load(contest, { ...ask, level: entry.code }),
        (set) => {
          if (JSON.stringify(ask) === scope) {
            extras = { ...extras, [entry.code]: set };
          }
        },
      );
    }
    const isClose =
      tiers.at(-1)?.kind === "parts" &&
      zoom >= (begins.at(-1) ?? Infinity) - 0.8;
    const isPriming =
      isSettled &&
      mosaic == null &&
      isParted(from) &&
      Object.keys(parts).length === 0;
    if (!isClose && !isPriming) {
      return;
    }
    const middle = gl.getCenter();
    const box = gl.getBounds();
    const apart = new Set(from.apart?.parts ?? []);
    const away = new Set((from.outlying ?? []).map((entry) => entry.k));
    const near = from.parts
      .flatMap((part, index) => {
        if (apart.has(index) || away.has(part.k)) {
          return [];
        }
        const i = from.places.k.indexOf(part.k);
        const x = from.places.x[i];
        const y = from.places.y[i];
        return x == null || y == null
          ? []
          : [
              {
                part,
                gap: Math.hypot(x - middle.lng, y - middle.lat),
                isIn: box.contains([x, y]),
              },
            ];
      })
      .toSorted((a, b) => Number(b.isIn) - Number(a.isIn) || a.gap - b.gap)
      .slice(0, isClose ? 8 : 1);
    for (const { part } of near) {
      grab(part);
    }
  }

  function grab(part: { unit: string }) {
    const scope = JSON.stringify(ask);
    if (baseAsk !== scope) {
      return;
    }
    fetch(
      `${scope}|part|${part.unit}`,
      async () =>
        load(contest, {
          unit: part.unit,
          vs: view.vs,
          group: view.group,
          round: view.round,
        }),
      (set) => {
        if (JSON.stringify(ask) === scope) {
          parts = { ...parts, [part.unit]: set };
        }
      },
    );
  }

  function done() {
    term = "";
    isSearching = false;
    search?.blur();
  }

  function seek(row: { k: number; above: Array<number> }) {
    view.picked = [row.k];
    const hit = stage.find(row.k);
    if (hit != null) {
      name(hit);
      focus(hit);
      return;
    }
    chase = row.k;
    const part = base?.parts.find((entry) => row.above.includes(entry.k));
    if (part != null) {
      grab(part);
    }
    for (const k of row.above) {
      const near = stage.find(k);
      if (near != null) {
        focus(near);
        break;
      }
    }
  }

  afterNavigate(({ type }) => {
    isArrived = true;
    if (type === "popstate") {
      const query = (page.shallow?.url ?? page.url).searchParams;
      view.read(query);
      camera = query.get("at");
    }
  });

  async function go(next: string) {
    const url = new URL(location.href);
    url.pathname = `/map/${[path, next].filter((part) => part !== "").join("/")}`;
    for (const key of ["at", "pick", "level"]) {
      url.searchParams.delete(key);
    }
    hover(null);
    await goto(url, { state: page.state, shallow: true }).catch(() => {});
    view.picked = [];
    view.level = null;
    camera = null;
  }

  function focus(hit: Hit) {
    if (stage.isApart(hit.set.places.k[hit.i] ?? 0)) {
      engine.reset();
      return;
    }
    const box = stage.shape(hit);
    if (box != null) {
      engine.fit(box);
      return;
    }
    const x = hit.set.places.x[hit.i];
    const y = hit.set.places.y[hit.i];
    if (x != null && y != null) {
      engine.fly([x, y], Math.max(zoom, (begins[hit.tier] ?? zoom) + 1.5));
    }
  }

  function summarize(hits: Array<Hit>) {
    const isCounted = hits.some(
      (hit) => (hit.set.places.valid[hit.i] ?? 0) > 0,
    );
    const totals = new Map<
      string,
      {
        label: string;
        past: string;
        votes: number;
        then: number;
        isThen: boolean;
      }
    >();
    let valid = 0;
    let previous = 0;
    let slack = 0;
    for (const { set, i } of hits) {
      const votes = isCounted ? (set.places.valid[i] ?? 0) : 1;
      const was = isCounted ? (set.places.previous[i] ?? votes) : 1;
      valid += votes;
      slack += votes / (set === mosaic?.set ? 2000 : 20_000);
      let isBefore = false;
      for (const [index, item] of set.legend.entries()) {
        const share = set.places.shares[index]?.[i];
        const before = set.places.before[index]?.[i];
        if (share == null && before == null) {
          continue;
        }
        const entry = totals.get(item.key) ?? {
          label:
            (hits.length === 1 ? set.places.people?.[index]?.[i] : null) ??
            labels.get(item.key) ??
            item.label,
          past: pasts.get(item.key) ?? item.then ?? item.label,
          votes: 0,
          then: 0,
          isThen: false,
        };
        entry.votes += ((share ?? 0) / 10_000) * votes;
        if (before != null) {
          entry.then += (before / 10_000) * was;
          entry.isThen = true;
          isBefore = true;
        }
        totals.set(item.key, entry);
      }
      if (isBefore) {
        previous += was;
      }
    }
    const rows = [...totals]
      .map(([key, entry]) => ({
        key,
        label: entry.label,
        past: entry.past,
        tone: lens.tone(key),
        share: valid === 0 ? 0 : entry.votes / valid,
        votes: isCounted ? entry.votes : null,
        then:
          isCompared && entry.isThen && previous > 0
            ? entry.then / previous
            : null,
      }))
      .filter((row) => row.share > 0 || (row.then ?? 0) > 0)
      .toSorted((a, b) => b.share - a.share)
      .map((row, index) => ({
        ...row,
        change: row.then == null ? null : row.share - row.then,
        isLeader: index === 0,
      }));
    const [first, second] = rows;
    const earlier = rows
      .filter((row) => row.then != null)
      .toSorted((a, b) => (b.then ?? 0) - (a.then ?? 0));
    const [was, runner] = earlier;
    return {
      valid: isCounted ? valid : 0,
      previous: isCounted && isCompared && previous > 0 ? previous : null,
      isExact: slack < 0.5,
      rows,
      verdict:
        first == null || second == null || first.share === 0
          ? null
          : {
              label: first.label,
              tone: first.tone,
              margin: first.share - second.share,
              then:
                was == null || runner == null
                  ? null
                  : {
                      label: was.past,
                      tone: was.tone,
                      margin: (was.then ?? 0) - (runner.then ?? 0),
                      isFlipped: was.key !== first.key,
                    },
            },
    };
  }

  function brief(hit: Hit) {
    const { valid, rows, verdict } = summarize([hit]);
    return {
      k: hit.set.places.k[hit.i] ?? 0,
      name: title(hit),
      valid,
      parts: rows.map((row) => ({
        key: row.key,
        tone: row.tone,
        share: row.share,
      })),
      leader: rows[0]?.label ?? "",
      tone: rows[0]?.tone ?? "var(--muted-foreground)",
      margin: verdict?.margin ?? null,
      swing:
        verdict?.then == null || verdict.then.isFlipped
          ? null
          : verdict.margin - verdict.then.margin,
      isFlipped: verdict?.then?.isFlipped ?? false,
    };
  }

  function inside(point: { x: number; y: number }, shape: Array<Point>) {
    let isIn = false;
    for (
      let at = 0, last = shape.length - 1;
      at < shape.length;
      last = at, at += 1
    ) {
      const a = shape[at];
      const b = shape[last];
      if (
        a != null &&
        b != null &&
        a.y > point.y !== b.y > point.y &&
        point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x
      ) {
        isIn = !isIn;
      }
    }
    return isIn;
  }

  function gather(shape: Array<Point>) {
    const gl = engine.gl;
    if (gl == null || shape.length < 3) {
      return;
    }
    const xs = shape.map((point) => point.x);
    const ys = shape.map((point) => point.y);
    const box = {
      west: Math.max(0, Math.min(...xs)),
      east: Math.min(room.width, Math.max(...xs)),
      north: Math.max(0, Math.min(...ys)),
      south: Math.min(room.height, Math.max(...ys)),
    };
    const step = Math.max(
      4,
      Math.sqrt(((box.east - box.west) * (box.south - box.north)) / 3000),
    );
    const found = new Set(view.picked);
    for (let y = box.north + step / 2; y < box.south; y += step) {
      for (let x = box.west + step / 2; x < box.east; x += step) {
        if (inside({ x, y }, shape)) {
          const hit = stage.pick({ x, y }, tier);
          if (hit != null) {
            found.add(hit.set.places.k[hit.i] ?? 0);
          }
        }
      }
    }
    for (const set of shown) {
      for (const [i, k] of set.places.k.entries()) {
        const x = set.places.x[i];
        const y = set.places.y[i];
        if (x == null || y == null || found.has(k)) {
          continue;
        }
        const point = gl.project([x, y]);
        if (inside(point, shape) && stage.find(k) != null) {
          found.add(k);
        }
      }
    }
    view.picked = [...found].slice(0, 5000);
  }

  function draw(event: PointerEvent) {
    const isBox = event.shiftKey && event.pointerType === "mouse";
    if (event.button !== 0 || (!isBox && !isAdding)) {
      return;
    }
    const target = event.currentTarget;
    if (lasso != null || !(target instanceof HTMLElement)) {
      lasso = null;
      return;
    }
    const frame = target.getBoundingClientRect();
    engine.gl?.dragPan.disable();
    lasso = {
      isBox,
      id: event.pointerId,
      left: frame.left,
      top: frame.top,
      points: [{ x: event.clientX - frame.left, y: event.clientY - frame.top }],
    };
  }

  function trace(event: PointerEvent) {
    const held = lasso;
    if (held == null || event.pointerId !== held.id) {
      return;
    }
    const point = { x: event.clientX - held.left, y: event.clientY - held.top };
    const last = held.points.at(-1);
    if (last == null || Math.hypot(point.x - last.x, point.y - last.y) < 3) {
      return;
    }
    if (
      event.currentTarget instanceof HTMLElement &&
      !event.currentTarget.hasPointerCapture(event.pointerId)
    ) {
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    lasso = {
      ...held,
      points: held.isBox
        ? [held.points[0] ?? point, point]
        : [...held.points, point],
    };
  }

  function close(event: PointerEvent) {
    const held = lasso;
    if (held == null || event.pointerId !== held.id) {
      return;
    }
    lasso = null;
    if (!isAdding) {
      engine.gl?.dragPan.enable();
    }
    const shape = held.isBox ? corners(held.points) : held.points;
    const xs = shape.map((point) => point.x);
    const ys = shape.map((point) => point.y);
    if (
      event.type === "pointerup" &&
      Math.max(...xs) - Math.min(...xs) + Math.max(...ys) - Math.min(...ys) > 12
    ) {
      gather(shape);
    }
  }

  function corners(points: Array<Point>) {
    const a = points[0];
    const b = points.at(-1);
    return a == null || b == null
      ? []
      : [a, { x: b.x, y: a.y }, b, { x: a.x, y: b.y }];
  }

  function save() {
    engine.compose()?.toBlob((blob) => {
      if (blob == null) {
        return;
      }
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = `${["map", ...path.split("/").filter((part) => part !== "")].join("-")}.png`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(href), 1000);
    }, "image/png");
  }

  function title(hit: Hit) {
    const found = hit.set.places.name[hit.i] ?? "";
    return found === "" ? "…" : found;
  }

  function noun(hit: Hit) {
    const held = mosaic;
    if (held != null && hit.set === held.set) {
      return held.layers[held.layer[hit.i] ?? 0]?.noun ?? "";
    }
    return (
      hit.set.levels.find((level) => level.code === hit.set.level)?.noun ??
      tiers[hit.tier]?.noun ??
      ""
    );
  }

  function where(hit: Hit) {
    const held = mosaic;
    const label = noun(hit);
    const upper = label.charAt(0).toUpperCase() + label.slice(1);
    const part =
      held != null && hit.set === held.set
        ? base?.parts[held.part[hit.i] ?? -1]
        : hit.set.scope.k !== base?.scope.k
          ? { name: hit.set.scope.name }
          : null;
    return part == null || part.name === ""
      ? upper
      : m.atlas_place_in({ noun: upper, place: part.name });
  }

  const picked = $derived.by(() => {
    void drawn;
    return view.picked.flatMap((k) => {
      const hit = stage.find(k);
      return hit == null ? [] : [hit];
    });
  });
  const overall = $derived.by(() => {
    const from = base;
    const at = above?.places.k.indexOf(head.scope.k) ?? -1;
    return summarize(
      above != null && at !== -1
        ? [{ set: above, i: at, tier: 0, source: "" }]
        : from == null
          ? []
          : from.places.k.map((_, i) => ({
              set: from,
              i,
              tier: 0,
              source: "",
            })),
    );
  });
  const summary = $derived.by(() => {
    void named;
    if (picked.length === 0) {
      return {
        ...overall,
        name: head.scope.name,
        kind: [head.title, head.date == null ? "" : date(head.date)]
          .filter((part) => part !== "")
          .join(", "),
        isCombined: false,
      };
    }
    const one = picked[0];
    return {
      ...summarize(picked),
      name:
        picked.length === 1 && one != null
          ? title(one)
          : m.atlas_picked({ count: picked.length }),
      kind: picked.length === 1 && one != null ? where(one) : head.scope.name,
      isCombined: picked.length > 1,
    };
  });
  const electoral = $derived.by(() => {
    if (electors == null || picked.length > 1) {
      return null;
    }
    const one = picked[0];
    const k = one == null ? head.scope.k : one.set.places.k[one.i];
    return k == null ? null : tally(electors, k);
  });
  function voter(row: Electors["rows"][number]) {
    const item =
      row.person == null
        ? null
        : legend.find((entry) => entry.person === row.person);
    return item == null
      ? swatch(row.color, row.key)
      : (tones.get(item.key) ?? swatch(item.color, item.key));
  }
  const glance = $derived.by(() => {
    void named;
    const hit = hovered;
    if (hit == null || pointer == null || !wide.current) {
      return null;
    }
    const { valid, rows } = summarize([hit]);
    return { name: title(hit), kind: where(hit), valid, rows };
  });
  const actions = $derived.by(() => {
    const one = picked.length === 1 ? picked[0] : null;
    if (one == null) {
      const crumb = head.trail.length > 1 ? head.trail.at(-2) : null;
      return crumb == null || picked.length > 0
        ? []
        : [
            {
              label: m.atlas_up({ place: crumb.name }),
              onclick: () => void go(crumb.unit),
            },
          ];
    }
    const k = one.set.places.k[one.i];
    const part = base?.parts.find((entry) => entry.k === k) ?? null;
    return [
      { label: m.atlas_zoom_to(), onclick: () => focus(one) },
      ...(part == null
        ? []
        : [
            {
              label: m.atlas_alone({ place: part.name }),
              onclick: () => void go(part.unit),
            },
            ...(whole == null
              ? [
                  {
                    label: m.atlas_open({ place: part.name }),
                    href: resolve("/[...path]", {
                      path: `${path}/${part.unit}`,
                    }),
                  },
                ]
              : []),
          ]),
    ];
  });
  const standing = $derived.by(() => {
    void named;
    const one = picked.length === 1 ? picked[0] : null;
    const { first, second } = lens;
    if (one == null || first == null || second == null) {
      return null;
    }
    const { set, i } = one;
    const a = set.legend.findIndex((item) => item.key === first);
    const b = set.legend.findIndex((item) => item.key === second);
    if (a === -1 || b === -1) {
      return null;
    }
    const spread = (column: "shares" | "before", at: number) => {
      const ahead = set.places[column][a]?.[at];
      const behind = set.places[column][b]?.[at];
      return ahead == null || behind == null ? null : (ahead - behind) / 10_000;
    };
    const margin = spread("shares", i);
    if (margin == null) {
      return null;
    }
    const held = mosaic != null && set === mosaic.set ? mosaic : null;
    const places: Array<{ name: string; margin: number; isOwn: boolean }> = [];
    let total = 0;
    let weighted = 0;
    let votes = 0;
    for (const at of set.places.k.keys()) {
      if (
        set.places.leader[at] == null ||
        (held != null &&
          (held.part[at] !== held.part[i] || held.layer[at] !== held.layer[i]))
      ) {
        continue;
      }
      total += 1;
      const value = spread("shares", at);
      if (value == null) {
        continue;
      }
      places.push({
        name: set.places.name[at] ?? "",
        margin: value,
        isOwn: at === i,
      });
      weighted += value * (set.places.valid[at] ?? 0);
      votes += set.places.valid[at] ?? 0;
    }
    const within =
      held == null
        ? set.scope.name
        : (base?.parts[held.part[i] ?? -1]?.name ?? head.scope.name);
    const side = (key: string) =>
      overall.rows.find(
        (row) => row.key === key || row.label === labels.get(key),
      )?.share ?? 0;
    const lead = side(first) - side(second);
    return {
      sides: [
        { label: labels.get(first) ?? "", tone: lens.tone(first) },
        { label: labels.get(second) ?? "", tone: lens.tone(second) },
      ] as [{ label: string; tone: string }, { label: string; tone: string }],
      place: { name: title(one), margin, before: spread("before", i) },
      marks: [
        {
          name: within,
          margin:
            within === head.scope.name
              ? lead
              : votes === 0
                ? 0
                : weighted / votes,
        },
        ...(within === head.scope.name
          ? []
          : [{ name: head.scope.name, margin: lead }]),
      ],
      within,
      siblings: {
        noun: noun(one),
        count: places.length,
        total,
        rank:
          1 +
          places.filter((entry) =>
            margin >= 0 ? entry.margin > margin : entry.margin < margin,
          ).length,
        places: places.toSorted((x, y) => x.margin - y.margin),
      },
    };
  });
  const listed = $derived.by(() => {
    void named;
    void drawn;
    if (picked.length > 1) {
      return isApart && picked.length <= 6
        ? { title: null, rows: picked.map((hit) => brief(hit)) }
        : null;
    }
    const from = picked.length === 0 ? base : null;
    if (from == null || from.places.k.length < 3) {
      return null;
    }
    const level =
      from.levels.find((entry) => entry.code === from.level)?.noun ?? "";
    return {
      title: m.atlas_largest({ places: plural(level) }),
      rows: from.places.valid
        .entries()
        .toArray()
        .toSorted(([, x], [, y]) => y - x)
        .slice(0, 5)
        .map(([i]) => brief({ set: from, i, tier: 0, source: "" })),
    };
  });
  const shown = $derived.by(() => {
    void drawn;
    const code = now?.code;
    const found: Array<Sheet> = [];
    if (now?.kind === "mosaic" && mosaic != null) {
      found.push(mosaic.set);
    } else if (now?.kind === "parts") {
      found.push(...Object.values(parts));
    } else if (base != null && code === base.level) {
      found.push(base);
    } else if (code != null && extras[code] != null) {
      found.push(extras[code]);
    }
    return found.length === 0 && base != null ? [base] : found;
  });
  const found = $derived.by(() => {
    const query = term.trim().toLocaleLowerCase();
    if (query.length < 2) {
      return [];
    }
    void drawn;
    const rows: Array<{
      hit: Hit;
      name: string;
      where: string;
      votes: number;
    }> = [];
    const seen = new Set<number>();
    for (const [index] of tiers.entries()) {
      for (const set of index === 0 && base != null
        ? [base]
        : [
            extras[tiers[index]?.code ?? ""],
            ...(tiers[index]?.kind === "mosaic" && mosaic != null
              ? [mosaic.set]
              : []),
            ...Object.values(parts),
          ]) {
        if (set == null) {
          continue;
        }
        for (const [i, label] of set.places.name.entries()) {
          const k = set.places.k[i] ?? 0;
          if (
            seen.has(k) ||
            label === "" ||
            !label.toLocaleLowerCase().includes(query)
          ) {
            continue;
          }
          const hit = stage.find(k);
          if (hit != null) {
            seen.add(k);
            rows.push({
              hit,
              name: label,
              where: where(hit),
              votes: set.places.valid[i] ?? 0,
            });
          }
        }
      }
    }
    return rows
      .toSorted(
        (a, b) =>
          Number(b.name.toLocaleLowerCase().startsWith(query)) -
            Number(a.name.toLocaleLowerCase().startsWith(query)) ||
          b.votes - a.votes,
      )
      .slice(0, 8);
  });
  const remote = createQuery(() => ({
    ...orpc.maps.places.queryOptions({ input: { id: contest, q: wanted } }),
    enabled: [...wanted].length >= 3,
    staleTime: 10 * 60_000,
    meta: { quiet: true },
  }));
  const elsewhere = createQuery(() => ({
    ...orpc.search.query.queryOptions({
      input: { q: wanted, limit: 5, types: ["election", "contest", "polity"] },
    }),
    enabled: [...wanted].length >= 3,
    staleTime: 5 * 60_000,
    meta: { quiet: true },
  }));
  const isLong = $derived([...term.trim()].length >= 3);
  const more = $derived.by(() => {
    if (!isLong || found.length >= 8) {
      return [];
    }
    const seen = new Set(found.map((row) => row.hit.set.places.k[row.hit.i]));
    return (remote.data ?? [])
      .filter((row) => !seen.has(row.k))
      .slice(0, 8 - found.length);
  });
  const archived = $derived(
    isLong
      ? (elsewhere.data ?? []).flatMap((row) =>
          row.path == null ? [] : [{ ...row, path: row.path }],
        )
      : [],
  );
  const isLooking = $derived(
    isLong &&
      (wanted !== term.trim() || remote.isFetching || elsewhere.isFetching),
  );
  const analysis = $derived.by(() => {
    if (!isAnalyzing || base == null) {
      return null;
    }
    void moved;
    void stage.version;
    void drawn;
    void room.width;
    void room.height;
    const { hits, isBounded } = stage.within(tier, isNational);
    const rows: Array<{
      k: number;
      name: string;
      where: string;
      valid: number;
      first: number | null;
      second: number | null;
      margin: number | null;
      before: number | null;
      swing: number | null;
      tone: string;
      hit: Hit;
    }> = [];
    for (const hit of hits) {
      const { set, i } = hit;
      const reading = read(set, i, lens);
      if (!admits(set, reading, lens)) {
        continue;
      }
      rows.push({
        k: set.places.k[i] ?? 0,
        name: title(hit),
        where: where(hit),
        valid: set.places.valid[i] ?? 0,
        first: share(set, i, lens.first),
        second: share(set, i, lens.second),
        margin: reading.isMeasured ? reading.margin : null,
        before: reading.swing == null ? null : reading.margin - reading.swing,
        swing: reading.swing,
        tone: hue(set, reading.leader, lens),
        hit,
      });
    }
    const places = plural(now?.noun ?? "");
    return {
      rows,
      title: isBounded
        ? m.atlas_in_view({ count: count(rows.length), places })
        : m.atlas_listed({ count: count(rows.length), places }),
    };
  });
  const exported = $derived.by(() => {
    const items = legend;
    const then = isCompared && year != null ? year : null;
    return {
      columns: [
        m.atlas_column_place(),
        m.atlas_column_within(),
        m.atlas_column_votes(),
        ...items.map((item) => item.label),
        ...(then == null ? [] : items.map((item) => `${item.label} ${then}`)),
        m.atlas_column_margin(),
        ...(then == null ? [] : [m.atlas_column_swing()]),
      ],
      table: () => {
        const rows = analysis?.rows ?? [];
        const chosen = new Set(view.picked);
        return (
          chosen.size > 0 ? rows.filter((row) => chosen.has(row.k)) : rows
        ).map((row) => {
          const { set, i } = row.hit;
          const round = (value: number | null) =>
            value == null ? null : Math.round(value * 100_000) / 1000;
          return [
            row.name,
            row.where,
            row.valid,
            ...items.map((item) => round(share(set, i, item.key))),
            ...(then == null
              ? []
              : items.map((item) => round(was(set, i, item.key)))),
            round(row.margin),
            ...(then == null ? [] : [round(row.swing)]),
          ];
        });
      },
    };
  });
  const isEstimated = $derived(
    isCompared &&
      (base?.estimated === true ||
        Object.values(extras).some((set) => set.estimated)),
  );
  const origin = initial.get("from");
  const back =
    origin != null && origin.startsWith("/") && !origin.startsWith("//")
      ? origin
      : null;
  const exit = $derived(
    back ?? resolve("/[...path]", { path: head.scope.path ?? path }),
  );
  const isScheduled = $derived(
    base != null &&
      base.places.k.length === 0 &&
      base.date != null &&
      base.date >= new Date().toISOString().slice(0, 10),
  );
  const isUnmapped = $derived(
    base != null &&
      !isScheduled &&
      base.places.leader.every((leader) => leader == null),
  );

  $effect(() => {
    const wanted = ask;
    let isStale = false;
    untrack(() => (pending += 1));
    isFailed = false;
    void load(contest, wanted)
      .then((set) => {
        if (!isStale) {
          baseAsk = JSON.stringify(wanted);
          fetched.clear();
          base = set;
          extras = {};
          parts = {};
        }
      })
      .catch(() => {
        if (!isStale) {
          isFailed = true;
        }
      })
      .finally(() => {
        pending -= 1;
      });
    return () => {
      isStale = true;
    };
  });

  const upper = $derived(
    head.trail.length > 1 ? (head.trail.at(-2)?.unit ?? null) : null,
  );

  $effect(() => {
    const crumb = upper;
    const wanted =
      crumb == null ? null : { ...ask, unit: crumb === "" ? null : crumb };
    let isStale = false;
    above = null;
    if (wanted != null) {
      void load(contest, wanted)
        .then((set) => {
          if (!isStale) {
            above = set;
          }
        })
        .catch(() => {});
    }
    return () => {
      isStale = true;
    };
  });

  $effect(() => {
    const isOn = isAdding;
    const gl = engine.gl;
    if (gl == null) {
      return;
    }
    if (isOn) {
      gl.dragPan.disable();
    } else {
      gl.dragPan.enable();
    }
  });

  $effect(() => {
    if (picked.length < 2 || picked.length > 6) {
      isApart = false;
    }
  });

  $effect(() => {
    const from = base;
    if (from == null) {
      return;
    }
    const wanted = from.parts.length > 0 && unit === "" && whole == null;
    let isStale = false;
    isSettled = !wanted;
    mosaic = null;
    if (!wanted) {
      return;
    }
    const start = async () =>
      loadMosaic(contest, { vs: view.vs, group: view.group }, from)
        .then((found) => {
          if (!isStale) {
            mosaic = found;
          }
        })
        .catch(() => {})
        .finally(() => {
          if (!isStale) {
            isSettled = true;
            deepen();
          }
        });
    const timer = setTimeout(() => void start(), 50);
    return () => {
      isStale = true;
      clearTimeout(timer);
    };
  });

  $effect(() => {
    const frame = base?.frame;
    const k = base?.scope.k;
    if (engine.gl == null || frame == null) {
      return;
    }
    untrack(() => {
      if (built !== "" && !built.startsWith(`${k}|`)) {
        engine.frame(frame, true);
      }
    });
  });

  $effect(() => {
    void tiers;
    void room;
    if (engine.gl != null) {
      untrack(measure);
    }
  });

  $effect(() => {
    const from = base;
    const list = tiers;
    const starts = begins;
    const gl = engine.gl;
    if (from == null || gl == null || starts.length !== list.length) {
      return;
    }
    untrack(() => {
      const scope = `${from.scope.k}|${JSON.stringify(ask)}|${locked?.code ?? ""}`;
      const codes = list.map((entry) => entry.code);
      const signature = `${scope}|${codes.join(",")}|${starts.map((value) => value.toFixed(2)).join(",")}`;
      if (signature === built) {
        return;
      }
      const [previous = ""] = built.split("|codes:");
      const old = built.split("|codes:")[1] ?? "";
      const isGrown =
        previous === scope &&
        old !== "" &&
        `${codes.join(",")}|${starts.map((value) => value.toFixed(2)).join(",")}`.startsWith(
          old.split("|")[0] ?? "",
        ) &&
        codes.length === (old.split("|")[0] ?? "").split(",").length + 1;
      const last = list.at(-1);
      if (isGrown && last != null) {
        stage.extend(last, starts.at(-1) ?? Infinity);
      } else {
        stage.reset(
          list,
          starts,
          locked == null ? null : 0,
          {
            outline: from.outline,
            k: from.scope.k,
            isNested: from.trail.length > 1,
          },
          from.parted,
          {
            apart: new Set(
              (from.apart?.parts ?? []).flatMap((index) => {
                const part = from.parts[index];
                return part == null ? [] : [part.k];
              }),
            ),
            off: new Set(
              (from.outlying ?? []).flatMap((entry) =>
                from.parts.some((part) => part.k === entry.k) ? [entry.k] : [],
              ),
            ),
          },
        );
      }
      built = `${scope}|codes:${codes.join(",")}|${starts.map((value) => value.toFixed(2)).join(",")}`;
      tier = locked != null ? 0 : active(starts, gl.getZoom(), null);
      drawn += 1;
      deepen();
    });
  });

  $effect(() => {
    void drawn;
    const from = base;
    const list = tiers;
    const loaded = extras;
    const pieces = parts;
    const held = mosaic;
    if (from == null || engine.gl == null) {
      return;
    }
    untrack(() => {
      const tiled = new Set(
        list.flatMap((entry) => (entry.kind === "level" ? [entry.tiles] : [])),
      );
      const shown = new Set<string | null>();
      for (const [index, entry] of list.entries()) {
        if (entry.kind === "mosaic" && held != null) {
          stage.drape(held, index);
          for (const set of layered(held)) {
            const tiles = set.levels.find(
              (level) => level.code === set.level,
            )?.tiles;
            if (tiles == null || !tiled.has(tiles) || shown.has(tiles)) {
              stage.show(set, index, SHARP);
            }
          }
        } else if (entry.kind === "parts") {
          for (const set of Object.values(pieces)) {
            const tiles = set.levels.find(
              (level) => level.code === set.level,
            )?.tiles;
            if (tiles != null && !tiled.has(tiles)) {
              stage.show(set, index);
            }
          }
        } else {
          const set = entry.code === from.level ? from : loaded[entry.code];
          if (set != null && stage.show(set, index)) {
            shown.add(entry.tiles);
          }
        }
      }
      stage.letter(tier);
    });
  });

  const total = $derived.by(() => {
    let sum = 0;
    for (const valid of base?.places.valid ?? []) {
      sum += valid ?? 0;
    }
    return sum;
  });
  const bubble = $derived.by(() => {
    void room.width;
    void room.height;
    return base == null || engine.gl == null
      ? 0
      : engine.bubbles(total, base.frame ?? undefined);
  });
  const isArrowed = $derived(lens.mode === "swing" && view.isArrows);
  let stretch = $state(0.1);

  $effect(() => {
    const mark = JSON.stringify([
      lens.mode,
      lens.baseline,
      bubble,
      isArrowed,
      lens.whom,
      lens.first,
      lens.second,
      lens.range,
      lens.only,
      lens.isFlippedOnly,
      lens.isBanded,
      view.picked,
      [...tones],
      stage.version,
      drawn,
    ]);
    if (engine.gl == null) {
      return;
    }
    untrack(() =>
      stage.paint(lens, new Set(view.picked), mark, bubble, isArrowed),
    );
  });

  $effect(() => {
    const next = term.trim();
    const timer = setTimeout(() => (wanted = next), 250);
    return () => clearTimeout(timer);
  });

  $effect(() => {
    void drawn;
    void parts;
    void extras;
    void stage.version;
    const k = chase;
    if (k == null) {
      return;
    }
    untrack(() => {
      const hit = stage.find(k);
      if (hit != null) {
        chase = null;
        name(hit);
        focus(hit);
      }
    });
  });

  $effect(() => {
    void stage.version;
    void drawn;
    const level = tier;
    const picked = new Set(view.picked);
    if (engine.gl == null) {
      return;
    }
    if (!isArrowed) {
      untrack(() => engine.arrows([], 0));
      return;
    }
    const items = stage.arrows(level, lens, picked);
    stretch = reach(items.map((item) => item.swing));
    untrack(() => engine.arrows(items, stretch));
  });

  $effect(() => {
    void tier;
    if (engine.gl != null) {
      untrack(() => {
        stage.letter(tier);
        deepen();
      });
    }
  });

  $effect(() => {
    const isDetailed = view.isBasemap;
    const isSplit = base?.apart != null;
    if (engine.gl != null) {
      untrack(() => basemap(engine, scheme, isDetailed, [], isSplit));
    }
  });

  $effect(() => {
    void view.params();
    const at = camera;
    if (isArrived) {
      untrack(() => view.sync({ at }));
    }
  });

  $effect(() => {
    void sheet;
    untrack(() => engine.hold());
  });
</script>

<svelte:window
  onkeydown={(event: KeyboardEvent) => {
    isShift = event.shiftKey || event.metaKey || event.ctrlKey;
    const target = event.target as HTMLElement | null;
    if (target?.closest("input, textarea, [contenteditable]") != null) {
      if (event.key === "Escape") {
        term = "";
        search?.blur();
      }
      return;
    }
    if (
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      target?.closest("[role='dialog'], [data-vaul-drawer]") != null
    ) {
      return;
    }
    const toggles: Record<string, () => void> = {
      "?": () => (isHelping = true),
      "[": () => switcher?.move?.(-1),
      "]": () => switcher?.move?.(1),
      g: () => (isSwitching = true),
      t: () => (isAnalyzing = !isAnalyzing),
      s: () => (isAdding = !isAdding),
      b: () => (view.isBasemap = !view.isBasemap),
      "0": () => engine.reset(),
    };
    const toggle = toggles[event.key];
    if (toggle != null) {
      event.preventDefault();
      toggle();
    } else if (event.key === "/") {
      event.preventDefault();
      isSearching = true;
      requestAnimationFrame(() => search?.focus());
    } else if (event.key === "Escape") {
      if (view.picked.length > 0) {
        view.picked = [];
      } else if (head.trail.length > 1) {
        void go(head.trail.at(-2)?.unit ?? "");
      }
    } else if (event.key === "+" || event.key === "=") {
      engine.zoomIn();
    } else if (event.key === "-") {
      engine.zoomOut();
    }
  }}
  onkeyup={(event: KeyboardEvent) => {
    isShift = event.shiftKey || event.metaKey || event.ctrlKey;
  }}
/>

<svelte:head>
  <title
    >{head.title.includes(head.scope.name)
      ? m.atlas_page_title_plain({ title: head.title })
      : m.atlas_page_title({
          title: head.title,
          place: head.scope.name,
        })}</title
  >
</svelte:head>

{#snippet finder()}
  <Command.Root
    shouldFilter={false}
    class="relative overflow-visible bg-transparent"
  >
    <Command.Input
      bind:ref={search}
      bind:value={term}
      placeholder={m.atlas_search_full()}
      aria-label={m.atlas_search_full()}
      onblur={() => {
        if (term === "") {
          isSearching = false;
        }
      }}
    />
    {#if term.trim().length >= 2}
      <Command.List
        class="absolute inset-x-0 inset-bs-full z-40 mbs-1 rounded-sm bg-popover p-1 shadow-menu ring-1 ring-border"
      >
        {#if isLooking && found.length === 0}
          <Command.Loading>{m.atlas_search_busy()}</Command.Loading>
        {:else}
          <Command.Empty>{m.atlas_search_none()}</Command.Empty>
        {/if}
        {#if found.length > 0 || more.length > 0}
          <Command.Group heading={m.atlas_search_places()}>
            {#each found as row (row.hit.set.places.k[row.hit.i])}
              <Command.Item
                value="here|{row.hit.set.places.k[row.hit.i]}"
                keywords={[row.name]}
                onSelect={() => {
                  done();
                  view.picked = [row.hit.set.places.k[row.hit.i] ?? 0];
                  focus(row.hit);
                }}
              >
                <span class="flex-1 truncate">{row.name}</span>
                <span class="shrink-0 text-xs text-muted-foreground"
                  >{row.where}</span
                >
              </Command.Item>
            {/each}
            {#each more as row (row.k)}
              <Command.Item
                value="there|{row.k}"
                keywords={[row.name]}
                onSelect={() => {
                  done();
                  seek(row);
                }}
              >
                <span class="flex-1 truncate">{row.name}</span>
                {#if row.within != null}
                  <span class="shrink-0 text-xs text-muted-foreground"
                    >{row.within}</span
                  >
                {/if}
              </Command.Item>
            {/each}
          </Command.Group>
        {/if}
        {#if archived.length > 0}
          <Command.Group heading={m.atlas_search_archive()}>
            {#each archived as row (row.id)}
              <Command.LinkItem
                href="/map/{row.path}"
                value="archive|{row.id}"
                keywords={[row.name]}
                onSelect={done}
              >
                <span class="flex-1 truncate">{row.name}</span>
                {#if row.context != null || row.date != null}
                  <span class="shrink-0 text-xs text-muted-foreground"
                    >{row.context ?? date(row.date ?? "")}</span
                  >
                {/if}
              </Command.LinkItem>
            {/each}
          </Command.Group>
        {/if}
      </Command.List>
    {/if}
  </Command.Root>
{/snippet}

{#snippet keys(list: Array<string>)}
  <span class="flex shrink-0 items-center gap-1">
    {#each list as key (key)}
      <kbd
        class="grid place-items-center rounded-xs bg-secondary px-1.5 font-figures text-xs font-semibold ring-1 ring-border block-6 min-inline-6"
        >{key}</kbd
      >
    {/each}
  </span>
{/snippet}

<Dialog.Root bind:open={isHelping}>
  <Dialog.Content class="sm:max-inline-sm">
    <Dialog.Title class="text-base font-semibold"
      >{m.atlas_shortcuts()}</Dialog.Title
    >
    <dl class="grid gap-2.5 text-[0.8125rem]">
      {#each [{ keys: ["/"], label: m.atlas_shortcut_search() }, { keys: ["G"], label: m.atlas_shortcut_switch() }, { keys: ["[", "]"], label: m.atlas_shortcut_step() }, { keys: ["+", "−"], label: m.atlas_shortcut_zoom() }, { keys: ["0"], label: m.map_reset() }, { keys: ["Esc"], label: m.atlas_shortcut_up() }, { keys: [m.atlas_shortcut_click()], label: m.atlas_shortcut_add() }, { keys: ["S"], label: m.atlas_select_many() }, { keys: ["T"], label: m.atlas_analysis() }, { keys: ["B"], label: m.map_basemap() }, { keys: ["?"], label: m.atlas_shortcut_help() }] as row (row.label)}
        <div class="flex items-center justify-between gap-4">
          <dt>{row.label}</dt>
          <dd>{@render keys(row.keys)}</dd>
        </div>
      {/each}
    </dl>
  </Dialog.Content>
</Dialog.Root>

{#snippet credits()}
  {#if engine.credits.length > 0 || view.isBasemap}
    <p class="text-[0.6875rem] text-pretty text-muted-foreground">
      {[
        ...engine.credits,
        ...(view.isBasemap ? [m.map_basemap_credit()] : []),
      ].join("; ")}
    </p>
  {/if}
{/snippet}

{#snippet details(layout: "full" | "peek" | "head" | "body")}
  <Result
    {summary}
    {year}
    {actions}
    {layout}
    isApart={listed != null && listed.title == null}
    onclear={view.picked.length > 0 ? () => (view.picked = []) : null}
    opening={electoral == null ? undefined : opening}
    headline={electoral?.isNational === true ? headline : undefined}
  />
  {#snippet opening()}
    {#if electors != null && electoral != null}
      <Electoral {electors} held={electoral} tone={voter} />
    {/if}
  {/snippet}
  {#snippet headline()}
    {#if electors != null && electoral != null}
      <Electoral {electors} held={electoral} tone={voter} isCompact />
    {/if}
  {/snippet}
  {#if layout === "full" || layout === "body"}
    {#if picked.length > 1 && picked.length <= 6}
      <div class="grid grid-cols-[minmax(0,1fr)]">
        <Segmented
          label={m.atlas_selection_view()}
          size="sm"
          fill
          options={[
            { value: "together", label: m.atlas_together() },
            { value: "apart", label: m.atlas_apart() },
          ]}
          value={isApart ? "apart" : "together"}
          onValueChange={(value) => {
            isApart = value === "apart";
          }}
        />
      </div>
    {/if}
    {#if listed != null}
      <Places
        title={listed.title}
        rows={listed.rows}
        {year}
        onpick={(k: number) => {
          const hit = stage.find(k);
          view.picked = [k];
          if (hit != null) {
            focus(hit);
          }
        }}
        onhover={(k: number | null) => hover(k == null ? null : stage.find(k))}
      />
    {/if}
    {#if standing != null}
      <Standing
        sides={standing.sides}
        place={standing.place}
        marks={standing.marks}
        within={standing.within}
        siblings={standing.siblings}
        year={year == null ? null : Number(year)}
        isCompact
      />
    {/if}
  {/if}
{/snippet}

{#snippet controls()}
  <Key
    {view}
    {lens}
    sets={shown}
    {legend}
    targets={base?.targets ?? []}
    {year}
    {isProportional}
    isAllied={base?.isAllied ?? false}
    isRunoff={base?.isRunoff ?? false}
    {isEstimated}
    noun={now?.noun ?? ""}
    scope={head.scope.name}
    {stretch}
  />
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
      <Switcher
        bind:this={switcher}
        bind:isOpen={isSwitching}
        {contest}
        {whole}
        {path}
        {unit}
        title={head.title}
        params={{ ...view.params(), at: camera }}
        isWide={wide.current}
      />
      <nav aria-label={m.atlas_trail()}>
        <ol
          class="flex items-center gap-1 text-[0.8125rem]/5 text-muted-foreground"
        >
          {#each head.trail as crumb, index (crumb.unit)}
            <li class="flex items-center gap-1 min-inline-0">
              {#if index > 0}
                <span aria-hidden="true">/</span>
              {/if}
              {#if index === head.trail.length - 1}
                <span
                  class="truncate font-semibold text-foreground"
                  aria-current="page">{crumb.name}</span
                >
              {:else}
                <a
                  href={resolve("/map/[...path]", {
                    path: [path, crumb.unit]
                      .filter((part) => part !== "")
                      .join("/"),
                  })}
                  class="truncate underline decoration-border underline-offset-3 hover:text-foreground hover:decoration-foreground"
                  onclick={(event) => {
                    event.preventDefault();
                    void go(crumb.unit);
                  }}>{crumb.name}</a
                >
              {/if}
            </li>
          {/each}
        </ol>
      </nav>
    </div>
    {#if wide.current}
      <div class="inline-[min(18rem,28vw)]">{@render finder()}</div>
    {:else}
      <button
        type="button"
        class="grid shrink-0 place-items-center rounded-sm block-9 inline-9 hover:bg-secondary"
        aria-label={m.atlas_search()}
        onclick={() => {
          isSearching = true;
          requestAnimationFrame(() => search?.focus());
        }}><SearchIcon class="block-4.5 inline-4.5" /></button
      >
    {/if}
    <a
      href={exit}
      class="flex shrink-0 items-center gap-1.5 rounded-sm px-2.5 text-[0.8125rem] font-semibold ring-1 ring-input block-8 hover:bg-secondary max-md:hidden"
      >{#if back != null}<ArrowLeftIcon
          class="block-3.5 inline-3.5"
          aria-hidden="true"
        />{m.atlas_back()}{:else}{m.atlas_results()}{/if}</a
    >
    <a
      href={exit}
      class="grid shrink-0 place-items-center rounded-sm block-9 inline-9 hover:bg-secondary"
      aria-label={back != null ? m.atlas_back() : m.atlas_close()}
      title={back != null ? m.atlas_back() : m.atlas_close()}
      ><XIcon class="block-4.5 inline-4.5" /></a
    >
    {#if isSearching && !wide.current}
      <div
        class="absolute inset-0 z-40 flex items-center gap-2 bg-card px-3"
        transition:fade={{ duration: calm.current ? 0 : 100 }}
      >
        <div class="flex-1">{@render finder()}</div>
        <button
          type="button"
          class="grid shrink-0 place-items-center rounded-sm block-9 inline-9 hover:bg-secondary"
          aria-label={m.atlas_close_search()}
          onclick={() => {
            term = "";
            isSearching = false;
          }}><XIcon class="block-4.5 inline-4.5" /></button
        >
      </div>
    {/if}
  </header>

  <div
    class={cn("grid min-block-0", {
      "grid-cols-[minmax(0,1fr)_clamp(17rem,30vw,24rem)]": wide.current,
    })}
  >
    <main class="relative overflow-hidden min-block-0">
      <div
        class="absolute inset-0"
        role="application"
        aria-roledescription="map"
        aria-label={m.map_label({ noun: now?.noun ?? "" })}
        aria-describedby="{uid}-hint"
        bind:clientWidth={room.width}
        bind:clientHeight={room.height}
        onpointerdowncapture={draw}
        onpointermove={trace}
        onpointerup={close}
        onpointercancel={close}
      >
        <div class="block-full inline-full" {@attach engine.mount}></div>
      </div>
      <Gaps {engine} overlay="top" />
      {#if lasso != null && lasso.points.length > 1}
        {const shape = $derived(
          lasso.isBox ? corners(lasso.points) : lasso.points,
        )}
        <svg
          class="pointer-events-none absolute inset-0 z-10 block-full inline-full"
          aria-hidden="true"
        >
          <polygon
            points={shape.map((point) => `${point.x},${point.y}`).join(" ")}
            class="fill-foreground/8 stroke-foreground"
            stroke-width="1.5"
            stroke-dasharray="5 4"
            stroke-linejoin="round"
          />
        </svg>
      {/if}
      <p id="{uid}-hint" class="sr-only">{m.atlas_hint()}</p>

      {#if glance != null && pointer != null}
        <Hover summary={glance} point={pointer} {room} />
      {/if}

      {#if isAdding && view.picked.length === 0 && pending === 0}
        <p
          class="pointer-events-none absolute inset-x-0 inset-bs-3 z-10 mx-auto rounded-sm bg-foreground px-3 py-1.5 text-center text-[0.8125rem] font-semibold text-pretty text-card shadow-menu inline-fit max-inline-[min(24rem,calc(100%-6rem))]"
          role="status"
          transition:fade={{ duration: calm.current ? 0 : 120 }}
        >
          {wide.current ? m.atlas_select_hint() : m.atlas_select_hint_touch()}
        </p>
      {:else if pending > 0 || base == null}
        <p
          class="pointer-events-none absolute inset-x-0 inset-bs-3 z-10 mx-auto flex items-center gap-2 rounded-sm bg-card/95 px-3 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border block-8 inline-fit"
          role="status"
          transition:fade={{ duration: calm.current ? 0 : 120 }}
        >
          <span
            class="animate-spin rounded-full border-2 border-border border-bs-foreground block-3.5 inline-3.5 motion-reduce:animate-none"
            aria-hidden="true"
          ></span>
          {m.atlas_loading()}
        </p>
      {:else if isFailed}
        <p
          class="absolute inset-x-0 inset-bs-3 z-10 mx-auto rounded-sm bg-card/95 px-3 py-2 text-[0.8125rem] font-semibold shadow-menu ring-1 ring-border inline-fit"
          role="alert"
        >
          {m.atlas_failed()}
        </p>
      {:else if isScheduled && base?.date != null}
        <p
          class="pointer-events-none absolute inset-x-0 inset-bs-3 z-10 mx-auto rounded-sm bg-card/95 px-3 py-2 text-center text-[0.8125rem] font-semibold text-pretty shadow-menu ring-1 ring-border inline-fit max-inline-[min(26rem,calc(100%-1.5rem))]"
          role="status"
        >
          {m.slate_scheduled({ date: date(base.date) })}
        </p>
      {:else if isUnmapped}
        <div
          class="absolute inset-x-0 inset-bs-3 z-10 mx-auto grid justify-items-center gap-2 rounded-sm bg-card/95 px-3.5 py-3 text-center shadow-menu ring-1 ring-border inline-fit max-inline-[min(24rem,calc(100%-1.5rem))]"
          role="status"
        >
          <p class="text-[0.8125rem] font-semibold text-pretty">
            {m.atlas_unmapped()}
          </p>
          <a
            href={exit}
            class="flex items-center rounded-sm px-2.5 text-[0.8125rem] font-semibold ring-1 ring-input block-8 hover:bg-secondary"
            >{m.atlas_unmapped_link()}</a
          >
        </div>
      {/if}

      {#if !isUnmapped}
        <div
          class={cn("absolute z-20", {
            "inset-e-3 inset-bs-3": wide.current,
            "inset-e-3 inset-bs-3 max-md:inset-bs-3": !wide.current,
          })}
        >
          <Rail
            chain={locked == null ? tiers : levels}
            choices={all}
            {begins}
            {zoom}
            active={tier}
            locked={locked?.code ?? null}
            isCompact={!wide.current || !tall.current}
            onlock={(code: string | null) => {
              view.level = code;
            }}
          />
        </div>
      {/if}

      <div
        class={cn("absolute z-20 grid gap-1.5", {
          "inset-s-3 inset-be-3": wide.current,
          "inset-e-3": !wide.current,
        })}
        style:inset-block-end={wide.current ? undefined : `${sheet + 12}px`}
      >
        <div
          class="grid overflow-hidden rounded-sm bg-card/95 shadow-menu ring-1 ring-border"
        >
          <button
            type="button"
            class="grid place-items-center block-9 inline-9 hover:bg-secondary"
            aria-label={m.map_zoom_in()}
            title={m.map_zoom_in()}
            onclick={() => engine.zoomIn()}
            ><PlusIcon class="block-4 inline-4" /></button
          >
          <button
            type="button"
            class="grid place-items-center border-bs block-9 inline-9 hover:bg-secondary"
            aria-label={m.map_zoom_out()}
            title={m.map_zoom_out()}
            onclick={() => engine.zoomOut()}
            ><MinusIcon class="block-4 inline-4" /></button
          >
          <button
            type="button"
            class="grid place-items-center border-bs block-9 inline-9 hover:bg-secondary"
            aria-label={m.map_reset()}
            title={m.map_reset()}
            onclick={() => engine.reset()}
            ><ScanIcon class="block-4 inline-4" /></button
          >
        </div>
        <div
          class="grid overflow-hidden rounded-sm bg-card/95 shadow-menu ring-1 ring-border"
        >
          <button
            type="button"
            class={cn("grid place-items-center block-9 inline-9", {
              "bg-foreground text-card": isAdding,
              "hover:bg-secondary": !isAdding,
            })}
            aria-label={m.atlas_select_many()}
            title={m.atlas_select_many()}
            aria-pressed={isAdding}
            onclick={() => (isAdding = !isAdding)}
            ><BoxSelectIcon class="block-4 inline-4" /></button
          >
          <button
            type="button"
            class={cn("grid place-items-center border-bs block-9 inline-9", {
              "bg-foreground text-card": view.isBasemap,
              "hover:bg-secondary": !view.isBasemap,
            })}
            aria-label={m.map_basemap()}
            title={m.map_basemap()}
            aria-pressed={view.isBasemap}
            onclick={() => (view.isBasemap = !view.isBasemap)}
            ><MapIcon class="block-4 inline-4" /></button
          >
          <button
            type="button"
            class={cn("grid place-items-center border-bs block-9 inline-9", {
              "bg-foreground text-card": isAnalyzing,
              "hover:bg-secondary": !isAnalyzing,
            })}
            aria-label={m.atlas_analysis()}
            title={m.atlas_analysis()}
            aria-pressed={isAnalyzing}
            onclick={() => (isAnalyzing = !isAnalyzing)}
            ><SheetIcon class="block-4 inline-4" /></button
          >
        </div>
        {#if wide.current}
          <button
            type="button"
            class="grid place-items-center rounded-sm bg-card/95 shadow-menu ring-1 ring-border block-9 inline-9 hover:bg-secondary"
            aria-label={m.atlas_shortcuts()}
            title={m.atlas_shortcuts()}
            aria-keyshortcuts="Shift+?"
            onclick={() => (isHelping = true)}
            ><KeyboardIcon class="block-4 inline-4" /></button
          >
        {/if}
      </div>

      {#if aside.length > 0 && analysis == null && lasso == null}
        <div
          class={cn("absolute z-10", {
            "inset-s-15 inset-be-3": wide.current,
            "inset-s-3": !wide.current,
          })}
          style:inset-block-end={wide.current ? undefined : `${sheet + 12}px`}
        >
          <Insets
            groups={aside}
            {lens}
            picked={view.picked}
            {hovered}
            {room}
            isWide={wide.current}
            isCalm={calm.current}
            onhover={(
              hit: Hit | null,
              point: { x: number; y: number } | null,
            ) => {
              hover(hit);
              pointer = point;
            }}
            onpick={choose}
          />
        </div>
      {/if}

      {#if outlying.length > 0 && analysis == null && lasso == null}
        <div
          class={cn("absolute z-10", {
            "inset-e-3 inset-be-3 max-inline-[50%]": wide.current,
            "inset-s-3 inset-e-16": !wide.current,
          })}
          style:inset-block-end={wide.current
            ? undefined
            : `${sheet + 12 + (aside.length > 0 ? 92 : 0)}px`}
        >
          <Outlying
            entries={outlying}
            {lens}
            {labels}
            picked={view.picked}
            isWide={wide.current}
            onhover={(
              hit: Hit | null,
              point: { x: number; y: number } | null,
            ) => {
              hover(hit);
              pointer = point;
            }}
            onpick={choose}
          />
        </div>
      {/if}

      {#if analysis != null}
        <div
          class={cn(
            "absolute z-30 overflow-clip bg-card shadow-menu ring-1 ring-border",
            {
              "inset-s-16 inset-e-3 inset-be-3 rounded-md block-[min(22rem,46%)]":
                wide.current,
              "inset-x-0 inset-be-0 rounded-t-lg block-[64%]": !wide.current,
            },
          )}
        >
          <Analysis
            rows={analysis.rows}
            sides={[
              labels.get(lens.first ?? "") ?? "",
              labels.get(lens.second ?? "") ?? "",
            ]}
            title={analysis.title}
            year={isCompared ? year : null}
            now={head.date?.slice(0, 4) ?? null}
            picked={view.picked}
            columns={exported.columns}
            table={exported.table}
            resolve={(css: string) => engine.read(css)}
            onpick={(ks: Array<number>) => {
              view.picked = ks;
            }}
            onhover={(k: number | null) =>
              hover(k == null ? null : stage.find(k))}
            onimage={save}
            onclose={() => {
              isAnalyzing = false;
              hover(null);
            }}
          />
        </div>
      {/if}

      {#if !wide.current}
        <Drawer
          room={room.height}
          bind:detent
          bind:height={sheet}
          label={m.atlas_key()}
        >
          {#snippet head()}
            <div class="px-4 pbs-2 pbe-3">
              {@render details(detent === "peek" ? "peek" : "head")}
            </div>
          {/snippet}
          <div class="grid gap-5 px-4 pbe-6">
            {#if detent !== "peek"}
              {@render details("body")}
            {/if}
            {#if !isUnmapped}
              {@render controls()}
            {/if}
            {@render credits()}
          </div>
        </Drawer>
      {/if}
    </main>

    {#if wide.current}
      <aside
        class="grid content-start gap-5 overflow-y-auto border-s bg-card p-4 min-block-0"
        aria-label={m.atlas_key()}
      >
        {@render details("full")}
        {#if !isUnmapped}
          <div class="border-bs pbs-4">{@render controls()}</div>
        {/if}
        {@render credits()}
      </aside>
    {/if}
  </div>
</div>
