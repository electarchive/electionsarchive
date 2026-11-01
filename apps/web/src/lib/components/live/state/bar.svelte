<script lang="ts">
  import type { Seating, Side } from "#lib/live/ballot.js";
  import { count } from "#lib/format.js";
  import { cn } from "#lib/utils.js";

  const { seats, isSmall = false }: { seats: Seating; isSmall?: boolean } =
    $props();

  type Kind = "won" | "ahead" | "holdover" | "waiting";

  const WAITING =
    "color-mix(in oklab, var(--muted-foreground) 20%, var(--background))";
  const OUTER: Array<Kind> = ["holdover", "won", "ahead", "waiting"];

  const segments = $derived.by(() => {
    const found: Array<{
      key: string;
      size: number;
      color: string;
      kind: Kind;
    }> = [];
    const push = (side: Side, order: Array<Kind>) => {
      for (const kind of order) {
        found.push({
          key: `${side.id}:${kind}`,
          size: side[kind],
          color: side.color,
          kind,
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
    });
    if (seats.right != null) {
      push(seats.right, OUTER.toReversed());
    }
    return found.filter((segment) => segment.size > 0);
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
        class="transition-[flex-grow] duration-700 block-full motion-reduce:transition-none"
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
      aria-hidden="true"
    >
      {#if seats.left != null}
        <span style:color={seats.left.color}
          >{seats.left.short} {count(tally(seats.left))}</span
        >
      {/if}
      {#if seats.right != null}
        <span style:color={seats.right.color}
          >{count(tally(seats.right))} {seats.right.short}</span
        >
      {/if}
    </div>
  {/if}
</div>
