<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { on } from "svelte/events";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import Preview from "./preview.svelte";

  const {
    parties,
    places,
    here,
    place,
    link,
    size,
    isComplete = true,
    change = null,
    partners = [],
    governing = [],
  }: {
    parties: Array<{
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      lean: number | null;
      seats: number;
      here: number;
      members: Array<{
        place: number;
        name: string | null;
        position: number | null;
      }>;
    }>;
    places: Array<{ name: string; path: string | null }>;
    here: number | null;
    place: string;
    link: { name: string; path: string | null } | null;
    size: number | null;
    isComplete?: boolean;
    change?: Record<string, number> | null;
    partners?: Array<Array<string>>;
    governing?: Array<string>;
  } = $props();

  const uid = $props.id();

  let hovered = $state<string | null>(null);
  let picked = $state<Array<string>>([]);
  let active = $state<number | null>(null);
  let isAllPaths = $state(false);
  let pointer = "mouse";
  let pressed: number | null = null;

  const ordered = $derived(
    parties.toSorted(
      (a, b) =>
        (a.lean ?? Infinity) - (b.lean ?? Infinity) || b.seats - a.seats,
    ),
  );
  const totals = $derived.by(() => {
    let [all, local] = [0, 0];
    for (const party of parties) {
      all += party.seats;
      local += party.here;
    }
    return { all, local };
  });
  const majority = $derived(Math.floor(totals.all / 2) + 1);
  const layout = $derived.by(() => {
    let best = { gap: 0, radii: [1], counts: [totals.all] };
    for (let rows = 1; rows <= 24; rows++) {
      const radii = Array.from({ length: rows }, (_, row) =>
        rows === 1 ? 1 : 0.4 + (0.6 * row) / (rows - 1),
      );
      let sum = 0;
      for (const radius of radii) {
        sum += radius;
      }
      const raw = radii.map((radius) => (totals.all * radius) / sum);
      const counts = raw.map((value) => Math.floor(value));
      let left = totals.all;
      for (const value of counts) {
        left -= value;
      }
      for (const { row } of raw
        .map((value, row) => ({ rest: value - Math.floor(value), row }))
        .toSorted((a, b) => b.rest - a.rest)
        .slice(0, left)) {
        counts[row] = (counts[row] ?? 0) + 1;
      }
      let gap = rows === 1 ? Infinity : 0.6 / (rows - 1);
      for (const [row, radius] of radii.entries()) {
        const seats = counts[row] ?? 0;
        gap = Math.min(gap, seats > 1 ? (Math.PI * radius) / (seats - 1) : gap);
      }
      if (!counts.includes(0) && gap > best.gap) {
        best = { gap, radii, counts };
      }
    }
    return best;
  });
  const dot = $derived(Math.min(0.42 * layout.gap, 0.5) * 96);
  const scale = $derived(Math.min(1, (0.06 * 96) / dot));
  const ring = $derived(Math.max(0.45, 0.24 * dot));
  const seats = $derived.by(() => {
    const positions: Array<{ angle: number; radius: number }> = [];
    for (const [row, radius] of layout.radii.entries()) {
      const count = layout.counts[row] ?? 0;
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
    const placed = positions.map((position, index) => ({
      x: 100 + position.radius * 96 * Math.cos(position.angle),
      y: 100 - position.radius * 96 * Math.sin(position.angle),
      angle: position.angle,
      party: "",
      local: false,
      index,
      member: null as (typeof parties)[number]["members"][number] | null,
    }));
    let start = 0;
    for (const party of ordered) {
      const block = placed.slice(start, start + party.seats);
      start += party.seats;
      const angle = ((block[0]?.angle ?? 0) + (block.at(-1)?.angle ?? 0)) / 2;
      const [x, y] = [100 + 67 * Math.cos(angle), 100 - 67 * Math.sin(angle)];
      for (const seat of block) {
        seat.party = party.key;
      }
      for (const seat of block
        .toSorted(
          (a, b) => Math.hypot(a.x - x, a.y - y) - Math.hypot(b.x - x, b.y - y),
        )
        .slice(0, link == null ? 0 : party.here)) {
        seat.local = true;
      }
      const local = party.members.filter(
        (member) => link != null && member.place === here,
      );
      const rest = party.members.filter(
        (member) => link == null || member.place !== here,
      );
      for (const seat of block) {
        seat.member =
          (seat.local ? local.shift() : rest.shift()) ??
          local.shift() ??
          rest.shift() ??
          null;
      }
    }
    return placed;
  });
  const colors = $derived(
    new Map(
      parties.map((party) => [party.key, swatch(party.color, party.key)]),
    ),
  );
  const labels = $derived(
    new Map(parties.map((party) => [party.key, party.label])),
  );
  const majorityAngle = $derived.by(() => {
    const before = seats[majority - 2];
    const after = seats[majority - 1];
    if (before == null || after == null) {
      return Math.PI / 2;
    }
    return (
      (Math.atan2(100 - before.y, before.x - 100) +
        Math.atan2(100 - after.y, after.x - 100)) /
      2
    );
  });
  const whole = $derived(
    link == null && (size === totals.all || (size == null && isComplete)),
  );
  const reach = $derived(96 + dot + 2);
  const top = $derived(whole ? reach + 12 : reach);
  const center = $derived(
    link == null
      ? { value: totals.all, of: whole ? null : size }
      : { value: totals.local, of: totals.all },
  );
  const font = $derived.by(() => {
    const hole = 38.4 - dot - 2;
    const width = 0.28 * count(center.value).length;
    return Math.min(
      30,
      center.of == null
        ? (hole - 2) / Math.hypot(width, 0.72)
        : hole / Math.hypot(width, 1.18),
    );
  });
  const caption = $derived.by(() => {
    if (link != null) {
      return m.seats_focus({
        count: count(totals.local),
        total: count(totals.all),
        place,
      });
    }
    if (whole) {
      return m.seats_majority({ count: count(majority) });
    }
    return size == null
      ? m.seats_filled({ count: count(totals.all) })
      : m.seats_part({ count: count(totals.all), total: count(size) });
  });
  const tip = $derived.by(() => {
    const seat = active == null ? undefined : seats[active];
    if (seat == null) {
      return null;
    }
    const where =
      seat.member == null || places.length < 2
        ? null
        : (places[seat.member.place] ?? null);
    return {
      seat,
      color: colors.get(seat.party),
      party: labels.get(seat.party) ?? "",
      name: seat.member?.name ?? null,
      place: where?.name ?? null,
      path:
        where?.path == null ||
        resolve("/[...path]", { path: where.path }) === page.url.pathname
          ? null
          : where.path,
      position: seat.member?.position ?? null,
      x: (seat.x - (100 - reach)) / (2 * reach),
      y: (seat.y - dot - (100 - top)) / (top + dot + 2),
    };
  });
  const lead = $derived(
    tip == null ? hovered : (seats[active ?? -1]?.party ?? null),
  );
  const coalition = $derived.by(() => {
    let held = 0;
    for (const party of parties) {
      if (picked.includes(party.key)) {
        held += party.seats;
      }
    }
    return held;
  });

  const paths = $derived.by(() => {
    const holders = Map.groupBy(
      ordered.filter((party) => party.seats > 0),
      (party) =>
        partners.find((pair) => pair.includes(party.key))?.[0] ?? party.key,
    )
      .values()
      .map((members) => {
        let seats = 0;
        for (const party of members) {
          seats += party.seats;
        }
        return { members, seats };
      })
      .toArray()
      .toSorted((a, b) => b.seats - a.seats)
      .slice(0, 14);
    const found: Array<{
      keys: Array<string>;
      units: Array<Array<string>>;
      seats: number;
      span: number;
    }> = [];
    for (let mask = 1; mask < 1 << holders.length; mask++) {
      const units = holders.filter((_, index) => mask & (1 << index));
      let held = 0;
      for (const unit of units) {
        held += unit.seats;
      }
      const smallest = Math.min(...units.map((unit) => unit.seats));
      if (held < majority || held - smallest >= majority) {
        continue;
      }
      const members = units.flatMap((unit) => unit.members);
      const leans = members.map((party) => party.lean ?? Infinity);
      found.push({
        keys: ordered
          .filter((party) => members.includes(party))
          .map((party) => party.key),
        units: units
          .toSorted(
            (a, b) =>
              (a.members[0]?.lean ?? Infinity) -
              (b.members[0]?.lean ?? Infinity),
          )
          .map((unit) => unit.members.map((party) => party.key)),
        seats: held,
        span: leans.length === 1 ? 0 : Math.max(...leans) - Math.min(...leans),
      });
    }
    return found.toSorted(
      (a, b) =>
        a.span - b.span || a.keys.length - b.keys.length || b.seats - a.seats,
    );
  });
  const shorts = $derived(
    new Map(parties.map((party) => [party.key, party.short ?? party.label])),
  );

  const track = (
    event: PointerEvent & { currentTarget: EventTarget & SVGSVGElement },
  ) => {
    if (event.type === "pointerdown") {
      pointer = event.pointerType;
      pressed = active;
    }
    const matrix = event.currentTarget.getScreenCTM();
    if (matrix == null) {
      return;
    }
    const { x, y } = new DOMPoint(event.clientX, event.clientY).matrixTransform(
      matrix.inverse(),
    );
    const radius = Math.hypot(x - 100, y - 100);
    const pad = dot + 0.5 * layout.gap * 96;
    if (
      radius < (layout.radii[0] ?? 1) * 96 - pad ||
      radius > (layout.radii.at(-1) ?? 1) * 96 + pad ||
      y > 100 + pad
    ) {
      active = null;
      return;
    }
    let best = { index: -1, distance: Infinity };
    for (const seat of seats) {
      const distance = (seat.x - x) ** 2 + (seat.y - y) ** 2;
      if (distance < best.distance) {
        best = { index: seat.index, distance };
      }
    }
    active = best.index < 0 ? null : best.index;
  };
</script>

<section
  class="col-span-12 grid content-start gap-3 bg-card p-3.5 lg:col-span-4"
  aria-labelledby="{uid}-title"
>
  <h2
    id="{uid}-title"
    class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
  >
    {m.seats_title()}<span class="sr-only">, {place}</span>
  </h2>
  <div
    class="relative mx-auto inline-full"
    style:max-inline-size="{26 * scale}rem"
  >
    <svg
      {@attach (svg: SVGSVGElement) =>
        on(window, "pointerdown", (event) => {
          if (!(event.target instanceof Node && svg.contains(event.target))) {
            active = null;
          }
        })}
      class={cn(
        "block touch-pan-y overflow-visible rounded-sm outline-none inline-full focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring",
        { "cursor-pointer": tip?.path != null },
      )}
      viewBox="{100 - reach} {100 - top} {2 * reach} {top + dot + 2}"
      role="listbox"
      tabindex="0"
      aria-label={whole ? `${count(totals.all)} ${m.seats_title()}` : caption}
      aria-describedby="{uid}-keys"
      aria-activedescendant={active == null
        ? undefined
        : `${uid}-seat-${active}`}
      onpointermove={track}
      onpointerdown={track}
      onmousedown={(event) => event.preventDefault()}
      onpointerleave={(event) => {
        if (event.pointerType === "mouse") {
          active = null;
        }
      }}
      onclick={() => {
        if (tip?.path != null && (pointer === "mouse" || pressed === active)) {
          void goto(resolve("/[...path]", { path: tip.path }));
        }
      }}
      onkeydown={(event) => {
        const moves: Record<string, number> = {
          ArrowRight: 1,
          ArrowDown: 1,
          ArrowLeft: -1,
          ArrowUp: -1,
          Home: -Infinity,
          End: Infinity,
        };
        const move = moves[event.key];
        if (move != null) {
          event.preventDefault();
          active = Math.min(
            seats.length - 1,
            Math.max(0, (active ?? (move > 0 ? -1 : seats.length)) + move),
          );
        } else if (event.key === "Escape") {
          active = null;
        } else if (event.key === "Enter" && tip?.path != null) {
          void goto(resolve("/[...path]", { path: tip.path }));
        }
      }}
      onfocus={(event) => {
        if (event.currentTarget.matches(":focus-visible")) {
          active ??= 0;
        }
      }}
      onblur={() => (active = null)}
    >
      {#if link != null}
        <g class="fill-foreground opacity-16" aria-hidden="true">
          {#each seats.filter((seat) => seat.local) as seat (seat.index)}
            <circle cx={seat.x} cy={seat.y} r={dot + 0.5 * layout.gap * 96} />
          {/each}
        </g>
      {/if}
      {#each seats as seat (seat.index)}
        {const lit = $derived(
          lead == null
            ? picked.length > 0
              ? picked.includes(seat.party)
              : link == null || seat.local
            : lead === seat.party,
        )}
        {const where = $derived(
          seat.member == null || places.length < 2
            ? null
            : places[seat.member.place]?.name,
        )}
        <circle
          id="{uid}-seat-{seat.index}"
          class="transition-[fill] duration-150 motion-reduce:transition-none"
          cx={seat.x}
          cy={seat.y}
          r={dot}
          role="option"
          aria-selected={active === seat.index}
          aria-label={[
            seat.member?.name,
            labels.get(seat.party),
            where,
            seat.member?.position == null
              ? null
              : m.seats_position({ position: seat.member.position }),
          ]
            .filter((part) => part != null)
            .join(", ")}
          style:fill={lit
            ? colors.get(seat.party)
            : `color-mix(in oklab, ${colors.get(seat.party)} 24%, var(--card))`}
        />
      {/each}
      {#if tip != null}
        <g
          class="pointer-events-none fill-none stroke-foreground"
          aria-hidden="true"
        >
          {#if tip.place != null}
            {#each seats.filter((seat) => seat.index !== tip.seat.index && seat.member != null && seat.member.place === tip.seat.member?.place) as seat (seat.index)}
              <circle
                class="opacity-55"
                cx={seat.x}
                cy={seat.y}
                r={dot + 0.6 * ring}
                stroke-width={0.6 * ring}
              />
            {/each}
          {/if}
          <circle
            cx={tip.seat.x}
            cy={tip.seat.y}
            r={dot + ring}
            stroke-width={ring}
          />
        </g>
      {/if}
      {#if whole}
        <line
          class="stroke-foreground"
          stroke-width="1.4"
          stroke-linecap="round"
          x1={100 + (96 + dot + 3) * Math.cos(majorityAngle)}
          y1={100 - (96 + dot + 3) * Math.sin(majorityAngle)}
          x2={100 + (96 + dot + 11) * Math.cos(majorityAngle)}
          y2={100 - (96 + dot + 11) * Math.sin(majorityAngle)}
        />
      {/if}
      <text
        class="pointer-events-none fill-foreground font-serif tabular-nums"
        style:font-size="{font}px"
        x="100"
        y={center.of == null ? 98 : 99 - 0.38 * font}
        text-anchor="middle">{count(center.value)}</text
      >
      {#if center.of != null}
        <text
          class="pointer-events-none fill-muted-foreground tabular-nums"
          style:font-size="{0.34 * font}px"
          x="100"
          y="99"
          text-anchor="middle">{m.seats_of({ count: count(center.of) })}</text
        >
      {/if}
    </svg>
    {#if tip != null}
      <div
        class={cn(
          "pointer-events-none absolute z-10 grid -translate-y-[calc(100%+0.5rem)] gap-0.5 rounded-sm bg-foreground px-2 py-1.5 text-[0.8125rem] leading-tight text-background shadow-sm inline-max max-inline-60",
          {
            "-translate-x-1/2": tip.x >= 0.25 && tip.x <= 0.75,
            "-translate-x-3": tip.x < 0.25,
            "-translate-x-[calc(100%-0.75rem)]": tip.x > 0.75,
          },
        )}
        style:left="{tip.x * 100}%"
        style:top="{tip.y * 100}%"
        aria-hidden="true"
      >
        <span class="flex items-center gap-1.5 font-semibold">
          <span
            class="shrink-0 rounded-xs block-2 inline-2"
            style:background-color={tip.color}
          ></span>
          {tip.name ?? tip.party}
        </span>
        {#if tip.name != null}
          <span class="opacity-75">{tip.party}</span>
        {/if}
        {#if tip.place != null}
          <span class="opacity-75"
            >{tip.place}{#if tip.position != null}, {m.seats_position({
                position: tip.position,
              })}{/if}</span
          >
        {/if}
      </div>
    {/if}
  </div>
  <p id="{uid}-keys" class="sr-only">{m.seats_keys()}</p>
  <p class="text-center text-[0.8125rem] text-pretty text-muted-foreground">
    {caption}
    {#if link?.path != null}
      <Preview
        class="font-semibold whitespace-nowrap text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        path={link.path}>{m.seats_whole({ place: link.name })}</Preview
      >
    {/if}
  </p>
  {#if whole && parties.length > 1}
    <div class="grid gap-1.5" aria-live="polite">
      <div class="flex items-baseline justify-between gap-3 text-[0.8125rem]">
        <span class={cn({ "text-muted-foreground": picked.length === 0 })}>
          {#if picked.length === 0}
            {m.seats_coalition_hint()}
          {:else if coalition >= majority}
            <span class="font-semibold">{count(coalition)}</span>
            {m.seats_coalition_over({ spare: count(coalition - majority) })}
          {:else}
            <span class="font-semibold">{count(coalition)}</span>
            {m.seats_coalition_under({ short: count(majority - coalition) })}
          {/if}
        </span>
        {#if picked.length > 0}
          <button
            type="button"
            class="shrink-0 font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
            onclick={() => (picked = [])}>{m.seats_coalition_clear()}</button
          >
        {/if}
      </div>
      <span
        class="relative flex overflow-hidden rounded-xs bg-muted block-2"
        aria-hidden="true"
      >
        {#each ordered.filter( (party) => picked.includes(party.key) ) as party (party.key)}
          <span
            class="block block-full"
            style:inline-size="{(party.seats / totals.all) * 100}%"
            style:background-color={colors.get(party.key)}
          ></span>
        {/each}
        <span
          class="absolute inset-y-0 bg-foreground inline-0.5"
          style:inset-inline-start="{(majority / totals.all) * 100}%"
        ></span>
      </span>
    </div>
  {/if}
  <ul class="grid gap-px text-sm/tight">
    {#each ordered as party (party.key)}
      <li
        class={cn(
          "relative flex items-center gap-2 rounded-xs px-1.5 py-1",
          { "bg-secondary": lead === party.key },
          { "text-muted-foreground": link != null && party.here === 0 },
          { "ring-1 ring-foreground ring-inset": picked.includes(party.key) },
        )}
        style:--party={colors.get(party.key)}
        onpointerenter={() => (hovered = party.key)}
        onpointerleave={() => (hovered = null)}
      >
        <span
          class={cn("shrink-0 rounded-xs bg-(--party) block-2.5 inline-2.5", {
            "opacity-35": link != null && party.here === 0,
          })}
        ></span>
        {#if whole && parties.length > 1}
          <button
            type="button"
            class="flex-1 truncate text-start font-semibold min-inline-0 after:absolute after:inset-0"
            aria-pressed={picked.includes(party.key)}
            onclick={() =>
              (picked = picked.includes(party.key)
                ? picked.filter((key) => key !== party.key)
                : [...picked, party.key])}>{party.short ?? party.label}</button
          >
        {:else}
          <span class="flex-1 truncate font-semibold min-inline-0"
            >{party.short ?? party.label}</span
          >
        {/if}
        {#if link == null}
          {#if change != null}
            {const moved = $derived(change[party.key])}
            <span
              class={cn("text-end font-mono text-xs tabular-nums inline-10", {
                "text-muted-foreground": moved == null || moved === 0,
              })}
              >{moved == null
                ? m.seats_change_new()
                : moved === 0
                  ? "0"
                  : `${moved > 0 ? "+" : "−"}${count(Math.abs(moved))}`}</span
            >
          {/if}
          <span class="font-mono text-[0.8125rem] font-semibold tabular-nums"
            >{count(party.seats)}</span
          >
          <span
            class="text-end font-mono text-[0.8125rem] text-muted-foreground tabular-nums inline-14"
            >{percent(party.seats / totals.all)}</span
          >
        {:else}
          <span class="font-mono text-[0.8125rem] font-semibold tabular-nums"
            >{count(party.here)}</span
          >
          <span
            class="text-end font-mono text-[0.8125rem] text-muted-foreground tabular-nums inline-16"
            >{m.seats_of({ count: count(party.seats) })}</span
          >
        {/if}
      </li>
    {/each}
  </ul>
  {#if whole && paths.length > 1}
    <div class="grid gap-2 border-bs pbs-3">
      <div class="grid gap-0.5">
        <h3 class="text-[0.8125rem] font-bold">{m.seats_paths_title()}</h3>
        <p class="text-xs text-muted-foreground">
          {m.seats_paths_lede({ count: count(majority) })}
        </p>
      </div>
      <ul class="grid gap-px">
        {#each isAllPaths ? paths : paths.slice(0, 6) as path (path.keys.join())}
          {const isPicked = $derived(
            picked.length === path.keys.length &&
              path.keys.every((key) => picked.includes(key)),
          )}
          <li>
            <button
              type="button"
              class={cn(
                "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-xs p-1.5 text-start  inline-full hover:bg-secondary",
                { "ring-1 ring-foreground ring-inset": isPicked },
              )}
              aria-pressed={isPicked}
              onclick={() => (picked = isPicked ? [] : [...path.keys])}
            >
              {const isGoverning = $derived(
                governing.length > 0 &&
                  governing.every((key) => path.keys.includes(key)) &&
                  path.keys.every((key) => governing.includes(key)),
              )}
              <span class="truncate text-[0.8125rem] font-semibold"
                >{#if isGoverning}<span
                    class="me-1.5 highlighter px-1 text-xs font-semibold"
                    >{m.seats_paths_governed()}</span
                  >{/if}{path.units
                  .map((unit) => unit.map((key) => shorts.get(key)).join("/"))
                  .join(" + ")}</span
              >
              <span class="font-mono text-[0.8125rem] tabular-nums"
                >{count(path.seats)}</span
              >
              <span
                class="relative col-span-2 flex overflow-hidden rounded-xs bg-muted block-1.5"
                aria-hidden="true"
              >
                {#each path.keys as key (key)}
                  <span
                    style:inline-size="{((parties.find(
                      (party) => party.key === key,
                    )?.seats ?? 0) /
                      totals.all) *
                      100}%"
                    style:background-color={colors.get(key)}
                  ></span>
                {/each}
                <span
                  class="absolute inset-y-0 bg-foreground inline-px"
                  style:inset-inline-start="{(majority / totals.all) * 100}%"
                ></span>
              </span>
            </button>
          </li>
        {/each}
      </ul>
      {#if paths.length > 6}
        <button
          type="button"
          class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
          onclick={() => (isAllPaths = !isAllPaths)}
          >{isAllPaths
            ? m.seats_paths_fewer()
            : m.seats_paths_more({ count: count(paths.length) })}</button
        >
      {/if}
    </div>
  {/if}
</section>
