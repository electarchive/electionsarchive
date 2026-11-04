<script lang="ts">
  import type { Seating, Side } from "#lib/live/ballot.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { seats, isSmall = false }: { seats: Seating; isSmall?: boolean } =
    $props();

  type Kind = "won" | "ahead" | "holdover" | "waiting";
  type Part = Kind | "gainedWon" | "gainedAhead";

  const WAITING =
    "color-mix(in oklab, var(--muted-foreground) 20%, var(--background))";
  const OUTER: Array<Part> = [
    "holdover",
    "won",
    "gainedWon",
    "ahead",
    "gainedAhead",
    "waiting",
  ];
  const BASE: Record<Part, Kind> = {
    holdover: "holdover",
    won: "won",
    gainedWon: "won",
    ahead: "ahead",
    gainedAhead: "ahead",
    waiting: "waiting",
  };

  const segments = $derived.by(() => {
    const found: Array<{
      key: string;
      size: number;
      color: string;
      kind: Kind;
      isGain: boolean;
    }> = [];
    const push = (side: Side, order: Array<Part>) => {
      for (const part of order) {
        found.push({
          key: `${side.id}:${part}`,
          size:
            part === "won"
              ? side.won - side.gainedWon
              : part === "ahead"
                ? side.ahead - side.gainedAhead
                : side[part],
          color: side.color,
          kind: BASE[part],
          isGain: part === "gainedWon" || part === "gainedAhead",
        });
      }
    };
    if (seats.left != null) {
      push(seats.left, OUTER);
    }
    for (const side of seats.rest) {
      push(side, OUTER);
    }
    found.push({
      key: "waiting",
      size: seats.waiting,
      color: "",
      kind: "waiting",
      isGain: false,
    });
    if (seats.right != null) {
      push(seats.right, OUTER.toReversed());
    }
    return found.filter((segment) => segment.size > 0);
  });

  const gained = $derived.by(() => {
    let found = 0;
    for (const side of [seats.left, ...seats.rest, seats.right]) {
      found += (side?.gainedWon ?? 0) + (side?.gainedAhead ?? 0);
    }
    return found;
  });

  function tally(side: Side) {
    return seats.isCounting
      ? side.won + side.ahead + side.holdover
      : side.before;
  }

  function fill(color: string, kind: Kind) {
    if (color === "") {
      return WAITING;
    }
    if (kind === "ahead") {
      return `color-mix(in oklab, ${color} 50%, var(--background))`;
    }
    if (kind === "waiting") {
      return `color-mix(in oklab, ${color} 18%, var(--background))`;
    }
    return color;
  }
</script>

<div class="grid gap-1">
  <div
    class={cn("relative flex overflow-hidden rounded-xs", {
      "block-5": !isSmall,
      "block-2.5": isSmall,
    })}
    role="img"
    aria-label={[seats.left, ...seats.rest, seats.right]
      .flatMap((side) =>
        side == null ? [] : [`${side.short} ${count(tally(side))}`],
      )
      .join(", ")}
  >
    {#each segments as segment (segment.key)}
      <span
        class={cn(
          "transition-[flex-grow] duration-700 block-full motion-reduce:transition-none",
          { "shadow-[inset_0_-3px_0_var(--foreground)]": segment.isGain },
        )}
        style:flex-grow={segment.size}
        style:background-color={segment.kind === "holdover"
          ? undefined
          : fill(segment.color, segment.kind)}
        style:background-image={segment.kind === "holdover"
          ? `repeating-linear-gradient(135deg, ${segment.color} 0 3px, color-mix(in oklab, ${segment.color} 40%, var(--background)) 3px 6px)`
          : undefined}
      ></span>
    {/each}
    <span
      class="absolute inset-y-0 bg-foreground inline-0.5"
      style:inset-inline-start="{(seats.majority / Math.max(1, seats.total)) *
        100}%"
      aria-hidden="true"
    ></span>
  </div>
  {#if !isSmall}
    <div
      class="flex justify-between gap-3 font-figures text-sm font-bold tabular-nums"
    >
      {#if seats.left != null}
        <span aria-hidden="true" style:color={seats.left.color}
          >{seats.left.short} {count(tally(seats.left))}</span
        >
      {/if}
      {#if gained > 0}
        <span
          class="flex items-center gap-1.5 self-center font-sans text-xs font-semibold text-muted-foreground"
          ><span
            class="bg-muted shadow-[inset_0_-3px_0_var(--foreground)] block-3 inline-4"
          ></span>{m.night_bar_gained({ count: count(gained) })}</span
        >
      {/if}
      {#if seats.right != null}
        <span aria-hidden="true" style:color={seats.right.color}
          >{count(tally(seats.right))} {seats.right.short}</span
        >
      {/if}
    </div>
  {/if}
</div>
