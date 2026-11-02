import { page } from "$app/state";
import { untrack } from "svelte";
import type { Mode } from "#lib/atlas/color.js";
import { MODES } from "#lib/atlas/color.js";
import { keep } from "#lib/linked.js";

function range(value: string | null): [number, number] | null {
  const [low, high] = (value ?? "")
    .split(",")
    .map((part) => Number(part) / 1000);
  return value == null ||
    low == null ||
    high == null ||
    !Number.isFinite(low) ||
    !Number.isFinite(high)
    ? null
    : [low, high];
}

function camera(value: string | null): [number, number, number] | null {
  const [x, y, z] = (value ?? "").split(",").map(Number);
  return value == null ||
    x == null ||
    y == null ||
    z == null ||
    [x, y, z].some((part) => !Number.isFinite(part))
    ? null
    : [x, y, z];
}

export class View {
  mode = $state<Mode>("winner");
  who = $state<string | null>(null);
  level = $state<string | null>(null);
  vs = $state<string | null>(null);
  group = $state<string | null>(null);
  range = $state<[number, number] | null>(null);
  only = $state<string | null>(null);
  isFlippedOnly = $state(false);
  isBasemap = $state(true);
  picked = $state.raw<Array<number>>([]);
  at: [number, number, number] | null = null;

  constructor() {
    this.read(untrack(() => page.url.searchParams));
  }

  read(query: { get: (key: string) => string | null }) {
    this.mode =
      MODES.find((choice) => choice === query.get("color")) ?? "winner";
    this.who = query.get("who");
    this.level = query.get("level");
    this.vs = query.get("vs");
    this.group = query.get("group") === "alliance" ? "alliance" : null;
    this.range = range(query.get("range"));
    this.only = query.get("only");
    this.isFlippedOnly = query.get("flipped") === "1";
    this.isBasemap = query.get("basemap") !== "off";
    this.picked = (query.get("pick") ?? "")
      .split(",")
      .map(Number)
      .filter((k) => Number.isSafeInteger(k) && k > 0);
    this.at = camera(query.get("at"));
  }

  params() {
    return {
      level: this.level,
      color: this.mode === "winner" ? null : this.mode,
      who: this.mode === "share" ? this.who : null,
      vs: this.vs,
      group: this.group,
      range:
        this.range == null
          ? null
          : this.range.map((value) => Math.round(value * 1000)).join(","),
      only: this.only,
      flipped: this.isFlippedOnly ? "1" : null,
      basemap: this.isBasemap ? null : "off",
      pick: this.picked.length === 0 ? null : this.picked.join(","),
    };
  }

  sync(extra: Record<string, string | null> = {}) {
    keep({ ...this.params(), ...extra });
  }
}
