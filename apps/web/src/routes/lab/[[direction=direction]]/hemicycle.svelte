<script lang="ts">
  import type { LabView } from "./view.svelte.js";
  import {
    majority,
    parties,
    partyColor,
    seatOrder,
    totalSeats,
  } from "./election.js";

  const {
    view,
    rows = 13,
    inner = 0.36,
    center = false,
    animate = false,
  }: {
    view: LabView;
    rows?: number;
    inner?: number;
    center?: boolean;
    animate?: boolean;
  } = $props();

  const seats = $derived.by(() => {
    const radii: Array<number> = [];
    let radiusSum = 0;
    for (let row = 0; row < rows; row++) {
      const radius = inner + ((1 - inner) * row) / (rows - 1);
      radii.push(radius);
      radiusSum += radius;
    }
    const raw = radii.map((radius) => (totalSeats * radius) / radiusSum);
    const counts = raw.map((value) => Math.floor(value));
    let assigned = 0;
    for (const value of counts) assigned += value;
    const remainders = raw
      .map((value, row) => ({ rest: value - Math.floor(value), row }))
      .toSorted((a, b) => b.rest - a.rest);
    for (const { row } of remainders) {
      if (assigned >= totalSeats) break;
      counts[row] = (counts[row] ?? 0) + 1;
      assigned += 1;
    }
    const positions: Array<{ angle: number; radius: number }> = [];
    for (const [row, radius] of radii.entries()) {
      const count = counts[row] ?? 0;
      for (let seat = 0; seat < count; seat++) {
        positions.push({
          angle:
            count === 1
              ? Math.PI / 2
              : Math.PI - (seat * Math.PI) / (count - 1),
          radius,
        });
      }
    }
    positions.sort((a, b) => b.angle - a.angle || a.radius - b.radius);
    const owners: Array<string> = [];
    for (const code of seatOrder) {
      const seatsWon = parties.find((party) => party.code === code)?.seats ?? 0;
      for (let seat = 0; seat < seatsWon; seat++) owners.push(code);
    }
    return positions.map((position, index) => ({
      x: 100 + position.radius * 96 * Math.cos(position.angle),
      y: 100 - position.radius * 96 * Math.sin(position.angle),
      party: owners[index] ?? "",
      index,
    }));
  });

  const majorityAngle = $derived.by(() => {
    const before = seats[majority - 2];
    const after = seats[majority - 1];
    if (before == null || after == null) return Math.PI / 2;
    return (
      (Math.atan2(100 - before.y, before.x - 100) +
        Math.atan2(100 - after.y, after.x - 100)) /
      2
    );
  });

  const dot = $derived(((1 - inner) / (rows - 1)) * 96 * 0.4);

  const ticks = $derived<Array<[number, number]>>([
    ...(center
      ? []
      : [
          [inner - dot / 96 - 0.13, inner - dot / 96 - 0.03] as [
            number,
            number,
          ],
        ]),
    [1 + dot / 96 + 0.03, 1 + dot / 96 + 0.11],
  ]);
</script>

<svg
  class={{ hemicycle: true, animate }}
  viewBox="0 -10 200 {center ? 114 : 112}"
  role="img"
  aria-label="{totalSeats} seats"
>
  {#each seats as seat (seat.index)}
    <circle
      class={{
        "hemicycle-seat": true,
        dim: view.party != null && view.party !== seat.party,
      }}
      cx={seat.x}
      cy={seat.y}
      r={dot}
      fill={partyColor(seat.party)}
      style:--i={seat.index}
      role="presentation"
      onpointerenter={() => (view.party = seat.party)}
      onpointerleave={() => (view.party = null)}
    />
  {/each}
  {#each ticks as [from, to], index (index)}
    <line
      class="hemicycle-majority"
      x1={100 + from * 96 * Math.cos(majorityAngle)}
      y1={100 - from * 96 * Math.sin(majorityAngle)}
      x2={100 + to * 96 * Math.cos(majorityAngle)}
      y2={100 - to * 96 * Math.sin(majorityAngle)}
    />
  {/each}
  {#if center}
    <text class="hemicycle-total" x="100" y="97" text-anchor="middle"
      >{totalSeats}</text
    >
  {/if}
</svg>

<style>
  .hemicycle {
    display: block;
    inline-size: 100%;
    max-inline-size: 560px;
    margin-inline: auto;
    block-size: auto;
    overflow: visible;
  }

  .hemicycle-seat {
    transition: opacity 160ms ease;
  }

  .hemicycle-seat.dim {
    opacity: 0.16;
  }

  .hemicycle-majority {
    stroke: var(--ink);
    stroke-width: 1.4;
    stroke-linecap: round;
  }

  .hemicycle-total {
    fill: var(--ink);
    font-family: var(--font-display);
    font-size: 26px;
    font-variant-numeric: lining-nums tabular-nums;
  }

  .animate .hemicycle-seat {
    animation: seat-in 420ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
    animation-delay: calc(var(--i) * 1.4ms);
  }

  @keyframes seat-in {
    from {
      opacity: 0;
      transform: translateY(6px);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .animate .hemicycle-seat {
      animation: none;
    }
  }
</style>
