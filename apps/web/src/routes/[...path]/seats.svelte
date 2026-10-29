<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { untrack } from "svelte";
  import { on } from "svelte/events";
  import Export from "#lib/components/export.svelte";
  import Name from "#lib/components/name.svelte";
  import { count, percent } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import Preview from "./preview.svelte";

  type Member = {
    place: number;
    name: string | null;
    position: number | null;
    person?: string | null;
    party?: string | null;
    affiliation?: string | null;
  };

  const {
    parties: given,
    places,
    here,
    place,
    link,
    size,
    vacant = 0,
    seceded = 0,
    held = 0,
    official = null,
    isComplete = true,
    change: moved = null,
    partners: pairs = [],
    governing: governed = [],
    chamber = null,
    title = null,
    isLinked = false,
    isUp = false,
    isCompact = false,
  }: {
    parties: Array<{
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      lean: number | null;
      near?: string | null;
      seats: number;
      here: number;
      members: Array<Member>;
    }>;
    places: Array<{ name: string; path: string | null }>;
    here: number | null;
    place: string;
    link: { name: string; path: string | null } | null;
    size: number | null;
    vacant?: number;
    seceded?: number;
    held?: number;
    official?: number | null;
    isComplete?: boolean;
    change?: Record<string, number> | null;
    partners?: Array<Array<string>>;
    governing?: Array<string>;
    chamber?: string | null;
    title?: string | null;
    isLinked?: boolean;
    isUp?: boolean;
    isCompact?: boolean;
  } = $props();

  const uid = $props.id();
  const S = 100;

  const folded = $derived.by(() => {
    const groups = Map.groupBy(
      given.filter((party) => party.seats > 0),
      (party) => party.label.trim().toLocaleLowerCase(),
    );
    const order = (member: Member) => places[member.place]?.name ?? "";
    return groups
      .values()
      .flatMap((group) => {
        const head = group.toSorted((a, b) => b.seats - a.seats)[0];
        if (head == null) {
          return [];
        }
        let [seats, local] = [0, 0];
        let delta: number | null | undefined;
        for (const party of group) {
          seats += party.seats;
          local += party.here;
          const step = moved?.[party.key];
          if (moved != null) {
            delta = step == null ? (delta ?? null) : (delta ?? 0) + step;
          }
        }
        const leans = group.flatMap((party) => party.lean ?? []);
        return [
          {
            ...head,
            keys: group.map((party) => party.key),
            lean: leans.length === 0 ? null : Math.min(...leans),
            seats,
            here: local,
            delta,
            members:
              group.length === 1
                ? head.members
                : group
                    .flatMap((party) => party.members)
                    .toSorted(
                      (a, b) =>
                        order(a).localeCompare(order(b), undefined, {
                          numeric: true,
                        }) ||
                        (a.position ?? Infinity) - (b.position ?? Infinity),
                    ),
          },
        ];
      })
      .toArray();
  });
  const owner = $derived(
    new Map(
      folded.flatMap((party) => party.keys.map((key) => [key, party.key])),
    ),
  );
  const partners = $derived(
    pairs.map((pair) => [...new Set(pair.map((key) => owner.get(key) ?? key))]),
  );
  const governing = $derived([
    ...new Set(governed.map((key) => owner.get(key) ?? key)),
  ]);

  let hovered = $state<string | null>(null);
  let picked = $state<Array<string>>(
    untrack(() =>
      isLinked
        ? (page.url.searchParams
            .get("coalition")
            ?.split(",")
            .map((key) => owner.get(key) ?? key)
            .filter((key) => folded.some((party) => party.key === key)) ?? [])
        : [],
    ),
  );

  $effect(() => {
    if (isLinked) {
      keep({ coalition: picked.length === 0 ? null : picked.join(",") });
    }
  });
  let active = $state<number | null>(null);
  let isAllPaths = $state(false);
  let pointer = "mouse";
  let pressed: number | null = null;

  const isUnplaced = (party: { key: string; lean: number | null }) =>
    party.lean == null && /^(?:party:)?[0-9a-f]{8}-/.test(party.key);
  const unplaced = $derived(folded.filter(isUnplaced));
  const ordered = $derived.by(() => {
    const known = folded
      .filter((party) => party.lean != null)
      .toSorted((a, b) => (a.lean ?? 0) - (b.lean ?? 0) || b.seats - a.seats);
    let rest = 0;
    for (const party of known) {
      rest += party.seats;
    }
    rest /= 2;
    let middle: number | null = null;
    for (const party of known) {
      rest -= party.seats;
      if (rest <= 0) {
        middle = party.lean;
        break;
      }
    }
    const spot = (party: (typeof folded)[number]) =>
      party.lean ??
      (isUnplaced(party)
        ? ((party.near == null
            ? null
            : folded.find((other) => other.keys.includes(party.near ?? ""))
                ?.lean) ??
            middle ??
            Infinity) + 0.005
        : Infinity);
    return folded.toSorted((a, b) => spot(a) - spot(b) || b.seats - a.seats);
  });
  const totals = $derived.by(() => {
    let [all, local] = [vacant + seceded + held, 0];
    for (const party of folded) {
      all += party.seats;
      local += party.here;
    }
    return { all, local };
  });
  const majority = $derived(Math.floor((official ?? totals.all) / 2) + 1);

  const layout = $derived.by(() => {
    const total = Math.max(1, totals.all);
    const fit = (rows: number) => {
      const thick =
        rows === 1
          ? Math.min(0.2, Math.PI / (2 * total))
          : (Math.PI * rows) / (2 * total + Math.PI * rows * (rows - 1));
      return { rows, thick, inner: 1 - 2 * (rows - 1) * thick };
    };
    let chosen = fit(1);
    for (let rows = 2; rows <= 40; rows++) {
      const next = fit(rows);
      if (next.inner <= 0.62) {
        chosen = next.inner >= 0.4 ? next : chosen;
        break;
      }
      chosen = next;
    }
    const { rows, thick, inner } = chosen;
    const radii = Array.from(
      { length: rows },
      (_, row) => inner + 2 * row * thick,
    );
    let room = 0;
    for (const radius of radii) {
      room += radius;
    }
    const raw = radii.map((radius) => (totals.all * radius) / room);
    const counts = raw.map((value) => Math.floor(value));
    let left = totals.all;
    for (const value of counts) {
      left -= value;
    }
    for (const { row } of raw
      .map((value, row) => ({ rest: value - Math.floor(value), row }))
      .toSorted((a, b) => b.rest - a.rest || b.row - a.row)
      .slice(0, left)) {
      counts[row] = (counts[row] ?? 0) + 1;
    }
    return { rows, thick, radii, counts };
  });
  const dot = $derived(0.84 * layout.thick * S);
  const ring = $derived(Math.max(0.6, 0.22 * dot));

  const seats = $derived.by(() => {
    const positions: Array<{ angle: number; radius: number }> = [];
    for (const [row, radius] of layout.radii.entries()) {
      const seated = layout.counts[row] ?? 0;
      const margin = Math.asin(Math.min(1, layout.thick / radius));
      for (let seat = 0; seat < seated; seat++) {
        positions.push({
          angle:
            seated === 1
              ? Math.PI / 2
              : Math.PI -
                margin -
                (seat * (Math.PI - 2 * margin)) / (seated - 1),
          radius,
        });
      }
    }
    positions.sort((a, b) => b.angle - a.angle || a.radius - b.radius);
    const placed = positions.map((position, index) => ({
      x: position.radius * S * Math.cos(position.angle),
      y: -position.radius * S * Math.sin(position.angle),
      angle: position.angle,
      party: "",
      local: false,
      index,
      member: null as Member | null,
    }));
    let start = 0;
    for (const party of ordered) {
      const block = placed.slice(start, start + party.seats);
      start += party.seats;
      const queue = [...party.members];
      for (const seat of block) {
        seat.party = party.key;
        seat.member = queue.shift() ?? null;
        seat.local =
          link != null &&
          (isUp ? seat.member != null : seat.member?.place === here);
      }
      if (link == null) {
        continue;
      }
      let missing = party.here;
      for (const seat of block) {
        if (seat.local) {
          missing -= 1;
        }
      }
      for (const seat of block) {
        if (missing <= 0) {
          break;
        }
        if (!seat.local && seat.member == null) {
          seat.local = true;
          missing -= 1;
        }
      }
    }
    for (const seat of placed.slice(start, start + seceded)) {
      seat.party = "~seceded";
    }
    for (const seat of placed.slice(start + seceded, start + seceded + held)) {
      seat.party = "~held";
    }
    return placed;
  });
  const colors = $derived(
    new Map(
      folded.map((party) => {
        const head =
          folded
            .filter((other) =>
              partners.some(
                (pair) => pair.includes(party.key) && pair.includes(other.key),
              ),
            )
            .toSorted((a, b) => b.seats - a.seats)[0] ?? party;
        return [party.key, swatch(head.color, head.key)];
      }),
    ),
  );
  const labels = $derived(
    new Map([
      ...folded.map((party) => [party.key, party.label] as const),
      ["~seceded", m.seats_seceded()] as const,
      ["~held", m.seats_held()] as const,
    ]),
  );
  const whole = $derived(
    isUp ||
      (link == null &&
        held === 0 &&
        (size === totals.all || (size == null && isComplete))),
  );
  const majorityAngle = $derived.by(() => {
    const before = seats[majority - 2];
    const after = seats[majority - 1];
    return before == null || after == null
      ? Math.PI / 2
      : (before.angle + after.angle) / 2;
  });
  const outer = $derived((layout.radii.at(-1) ?? 1) * S + dot);
  const box = $derived({
    x: -outer - 1,
    y: -outer - (whole ? 10 : 1),
    width: 2 * outer + 2,
    height: outer + (whole ? 10 : 1) + 1,
  });
  const center = $derived(
    link == null
      ? held > 0
        ? { value: totals.all - held, of: totals.all }
        : { value: totals.all, of: whole ? null : size }
      : { value: totals.local, of: totals.all },
  );
  const font = $derived.by(() => {
    const hole = (layout.radii[0] ?? 0.5) * S - dot - 1.5;
    const digits = count(center.value).length;
    return Math.max(
      6,
      Math.min(
        0.66 * hole,
        (1.7 * hole) / (0.58 * digits + 0.2),
        center.of == null ? Infinity : 0.62 * hole,
      ),
    );
  });
  const caption = $derived.by(() => {
    if (held > 0) {
      return m.seats_up({
        count: count(totals.all - held),
        total: count(totals.all),
      });
    }
    if (isUp) {
      return m.seats_up({
        count: count(totals.local),
        total: count(totals.all),
      });
    }
    if (link != null) {
      return m.seats_focus({
        count: count(totals.local),
        total: count(totals.all),
        place,
      });
    }
    if (official != null) {
      return m.seats_official({
        count: count(official),
        majority: count(majority),
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
    const party = labels.get(seat.party) ?? m.seats_vacant();
    const own = seat.member?.affiliation ?? null;
    return {
      seat,
      color: colors.get(seat.party),
      party,
      own:
        own == null ||
        own.trim().toLocaleLowerCase() === party.toLocaleLowerCase()
          ? null
          : own,
      name: seat.member?.name ?? null,
      place: where?.name ?? null,
      path:
        where?.path == null ||
        resolve("/[...path]", { path: where.path }) === page.url.pathname
          ? null
          : where.path,
      position: seat.member?.position ?? null,
      isUnplaced: unplaced.some((party) => party.key === seat.party),
      x: (seat.x - box.x) / box.width,
      y: (seat.y - dot - box.y) / box.height,
    };
  });
  const lead = $derived(
    tip == null ? hovered : (seats[active ?? -1]?.party ?? null),
  );
  const coalition = $derived.by(() => {
    let held = 0;
    for (const party of folded) {
      if (picked.includes(party.key)) {
        held += party.seats;
      }
    }
    return held;
  });

  const paths = $derived.by(() => {
    const holders = Map.groupBy(
      ordered.filter((party) => !party.key.startsWith("category:")),
      (party) =>
        partners.find((pair) => pair.includes(party.key))?.[0] ?? party.key,
    )
      .values()
      .map((members) => {
        let held = 0;
        for (const party of members) {
          held += party.seats;
        }
        return { members, seats: held };
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
    new Map(folded.map((party) => [party.key, party.short ?? party.label])),
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
    const radius = Math.hypot(x, y);
    const pad = dot + layout.thick * S;
    if (
      radius < (layout.radii[0] ?? 0.5) * S - pad ||
      radius > outer + pad ||
      y > pad
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
  class={cn(
    "relative grid grid-cols-[minmax(0,1fr)] content-start gap-3 min-inline-0",
    { "bg-card p-3.5": !isCompact },
  )}
  aria-labelledby="{uid}-title"
  data-export={isCompact ? undefined : ""}
>
  {#if !isCompact}
    <Export name="seats" class="absolute inset-e-2 inset-bs-2" />
  {/if}
  <h2
    id="{uid}-title"
    class={cn({
      "pe-8 heading": !isCompact,
      "text-[0.8125rem] font-bold": isCompact,
    })}
  >
    {title ?? (isUp ? m.seats_after_title() : m.seats_title())}<span
      class="sr-only">, {place}</span
    >
  </h2>
  <figure class="grid justify-items-center gap-2">
    <div
      class="relative inline-full"
      style:max-inline-size="{Math.min(26, 11 + 2.4 * layout.rows)}rem"
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
          { "pointer-events-none": isCompact },
        )}
        viewBox="{box.x} {box.y} {box.width} {box.height}"
        role="listbox"
        tabindex={isCompact ? -1 : 0}
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
          if (
            tip?.path != null &&
            (pointer === "mouse" || pressed === active)
          ) {
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
            class={cn(
              "animate-seat transition-[fill] duration-150 motion-reduce:animate-none motion-reduce:transition-none",
              { "stroke-foreground": link != null && seat.local },
            )}
            style:animation-delay="{Math.round(
              (seat.index / seats.length) * 640,
            )}ms"
            cx={seat.x}
            cy={seat.y}
            role="option"
            aria-selected={active === seat.index}
            aria-label={[
              seat.member?.name,
              labels.get(seat.party) ?? m.seats_vacant(),
              where,
              seat.member?.position == null
                ? null
                : m.seats_position({ position: seat.member.position }),
            ]
              .filter((part) => part != null)
              .join(", ")}
            r={seat.party === "" || seat.party === "~seceded"
              ? dot - 0.3 * ring
              : dot}
            stroke-dasharray={seat.party === "~seceded"
              ? `${0.9 * ring} ${0.7 * ring}`
              : undefined}
            stroke-width={seat.party === "" || seat.party === "~seceded"
              ? 0.6 * ring
              : link != null && seat.local
                ? 0.45 * ring
                : undefined}
            style:stroke={seat.party === "" || seat.party === "~seceded"
              ? "var(--muted-foreground)"
              : null}
            style:fill={seat.party === "" || seat.party === "~seceded"
              ? "var(--card)"
              : seat.party === "~held"
                ? "color-mix(in oklab, var(--muted-foreground) 22%, var(--card))"
                : lit
                  ? colors.get(seat.party)
                  : `color-mix(in oklab, ${colors.get(seat.party)} 28%, var(--card))`}
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
                  class="opacity-50"
                  cx={seat.x}
                  cy={seat.y}
                  r={dot + 0.5 * ring}
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
        {#if whole && totals.all > 1}
          <line
            class="stroke-foreground"
            stroke-width="1.2"
            stroke-linecap="round"
            x1={(outer + 2) * Math.cos(majorityAngle)}
            y1={-(outer + 2) * Math.sin(majorityAngle)}
            x2={(outer + 8) * Math.cos(majorityAngle)}
            y2={-(outer + 8) * Math.sin(majorityAngle)}
          />
        {/if}
        <text
          class="pointer-events-none fill-foreground font-figures font-[780] tabular-nums"
          style:font-size="{font}px"
          x="0"
          y={center.of == null ? -0.5 : -0.42 * font - 1}
          text-anchor="middle">{count(center.value)}</text
        >
        {#if center.of != null}
          <text
            class="pointer-events-none fill-muted-foreground font-figures tabular-nums"
            style:font-size="{0.3 * font}px"
            x="0"
            y="-0.5"
            text-anchor="middle">{m.seats_of({ count: count(center.of) })}</text
          >
        {/if}
      </svg>
      {#if tip != null}
        <div
          class={cn(
            "pointer-events-none absolute z-10 grid -translate-y-[calc(100%+0.5rem)] gap-0.5 rounded-sm bg-foreground px-2.5 py-2 text-[0.8125rem]/tight  text-background shadow-sm inline-max max-inline-64",
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
          {#if tip.name != null}
            <span class="font-semibold">{tip.name}</span>
          {/if}
          <span
            class={cn("flex items-center gap-1.5", {
              "font-semibold": tip.name == null,
              "opacity-80": tip.name != null,
            })}
          >
            <span
              class={cn("shrink-0 rounded-xs block-2 inline-2", {
                "ring-1 ring-background/70 ring-inset": tip.color == null,
              })}
              style:background-color={tip.color}
            ></span>
            {tip.own ?? tip.party}
          </span>
          {#if tip.place != null}
            <span class="opacity-80"
              >{tip.place}{#if tip.position != null}, {m.seats_position({
                  position: tip.position,
                })}{/if}</span
            >
          {/if}
          {#if tip.isUnplaced}
            <span class="opacity-80">{m.seats_unplaced()}</span>
          {/if}
        </div>
      {/if}
    </div>
    <figcaption
      class="text-center text-[0.8125rem] text-pretty text-muted-foreground"
    >
      {caption}
      {#if link?.path != null}
        <Preview
          class="font-semibold whitespace-nowrap text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          path={link.path}>{m.seats_whole({ place: link.name })}</Preview
        >
      {/if}
    </figcaption>
  </figure>
  <p id="{uid}-keys" class="sr-only">{m.seats_keys()}</p>
  {#if whole && folded.length > 1 && !isCompact}
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
  {#if link == null}
    <div
      class="flex items-end gap-2 border-be px-1.5 pbe-1 text-xs text-muted-foreground"
    >
      <span class="flex-1 ps-4.5">{m.seats_col_party()}</span>
      {#if moved != null}
        <span class="text-end inline-12">{m.seats_col_change()}</span>
      {/if}
      <span class="text-end inline-10">{m.seats_col_seats()}</span>
      <span class="text-end inline-16">{m.seats_col_share()}</span>
    </div>
  {/if}
  <ul class="grid grid-cols-[minmax(0,1fr)] gap-px text-sm/tight">
    {#each ordered.toSorted((a, b) => b.seats - a.seats) as party (party.key)}
      {const id = $derived(
        /^(?:party:)?([0-9a-f]{8}-[0-9a-f-]{27})$/.exec(party.key)?.[1] ?? null,
      )}
      {const isMuted = $derived(link != null && party.here === 0)}
      <li
        class={cn(
          "relative flex items-center gap-2 rounded-xs px-1.5 py-1 pointer-coarse:py-2",
          { "bg-secondary": lead === party.key },
          { "text-muted-foreground": isMuted },
          { "ring-1 ring-foreground ring-inset": picked.includes(party.key) },
        )}
        onpointerenter={() => {
          if (!isCompact) {
            hovered = party.key;
          }
        }}
        onpointerleave={() => (hovered = null)}
      >
        {#if whole && folded.length > 1 && !isCompact}
          <button
            type="button"
            class="absolute inset-0 rounded-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-pressed={picked.includes(party.key)}
            aria-label={party.label}
            onclick={() =>
              (picked = picked.includes(party.key)
                ? picked.filter((key) => key !== party.key)
                : [...picked, party.key])}
          ></button>
        {/if}
        <span
          class={cn("shrink-0 rounded-xs block-2.5 inline-2.5", {
            "opacity-35": isMuted,
          })}
          style:background-color={colors.get(party.key)}
        ></span>
        <span class="flex-1 truncate font-semibold min-inline-0">
          <Name
            class="relative z-1 truncate align-bottom max-inline-full"
            label={party.short ?? party.label}
            hasSwatch={false}
            party={id}
          />
        </span>
        {#if link == null}
          {#if moved != null}
            <span
              class={cn(
                "text-end font-figures text-xs tabular-nums inline-12",
                {
                  "text-muted-foreground":
                    party.delta == null || party.delta === 0,
                },
              )}
              >{party.delta == null
                ? m.seats_change_new()
                : party.delta === 0
                  ? "0"
                  : `${party.delta > 0 ? "+" : "−"}${count(Math.abs(party.delta))}`}</span
            >
          {/if}
          <span
            class="text-end font-figures text-[0.8125rem] font-semibold tabular-nums inline-10"
            >{count(party.seats)}</span
          >
          <span
            class="text-end font-figures text-[0.8125rem] text-muted-foreground tabular-nums inline-16"
            >{percent(party.seats / (totals.all - held))}</span
          >
        {:else}
          <span class="font-figures text-[0.8125rem] font-semibold tabular-nums"
            >{count(party.here)}</span
          >
          <span
            class="text-end font-figures text-[0.8125rem] text-muted-foreground tabular-nums inline-16"
            >{m.seats_of({ count: count(party.seats) })}</span
          >
        {/if}
      </li>
    {/each}
    {#if seceded > 0}
      <li
        class="flex items-center gap-2 rounded-xs px-1.5 py-1 text-muted-foreground"
      >
        <span
          class="shrink-0 rounded-xs border border-dashed border-muted-foreground block-2.5 inline-2.5"
        ></span>
        <span class="flex-1 truncate font-semibold min-inline-0"
          >{m.seats_seceded()}</span
        >
        {#if moved != null}
          <span class="inline-12"></span>
        {/if}
        <span
          class="text-end font-figures text-[0.8125rem] font-semibold tabular-nums inline-10"
          >{count(seceded)}</span
        >
        <span
          class="text-end font-figures text-[0.8125rem] tabular-nums inline-16"
          >{percent(seceded / totals.all)}</span
        >
      </li>
    {/if}
    {#if held > 0}
      <li
        class="flex items-center gap-2 rounded-xs px-1.5 py-1 text-muted-foreground"
      >
        <span
          class="shrink-0 rounded-xs bg-muted-foreground/22 block-2.5 inline-2.5"
        ></span>
        <span class="flex-1 truncate font-semibold min-inline-0"
          >{m.seats_held()}</span
        >
        {#if moved != null}
          <span class="inline-12"></span>
        {/if}
        <span
          class="text-end font-figures text-[0.8125rem] font-semibold tabular-nums inline-10"
          >{count(held)}</span
        >
        <span class="inline-16"></span>
      </li>
    {/if}
    {#if vacant > 0}
      <li
        class="flex items-center gap-2 rounded-xs px-1.5 py-1 text-muted-foreground"
      >
        <span
          class="shrink-0 rounded-xs ring-1 ring-muted-foreground block-2.5 inline-2.5 ring-inset"
        ></span>
        <span class="flex-1 truncate font-semibold min-inline-0"
          >{m.seats_vacant()}</span
        >
        {#if moved != null}
          <span class="inline-12"></span>
        {/if}
        <span
          class="text-end font-figures text-[0.8125rem] font-semibold tabular-nums inline-10"
          >{count(vacant)}</span
        >
        <span
          class="text-end font-figures text-[0.8125rem] tabular-nums inline-16"
          >{percent(vacant / totals.all)}</span
        >
      </li>
    {/if}
  </ul>
  {#if unplaced.length > 0 && !isCompact}
    <p class="text-xs text-muted-foreground">
      {m.seats_unplaced_note({
        parties: unplaced.map((party) => party.short ?? party.label).join(", "),
      })}
    </p>
  {/if}
  {#if whole && paths.length > 1 && !isCompact}
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
                "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-xs p-1.5 text-start inline-full hover:bg-secondary",
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
              <span class="font-figures text-[0.8125rem] tabular-nums"
                >{count(path.seats)}</span
              >
              <span
                class="relative col-span-2 flex overflow-hidden rounded-xs bg-muted block-1.5"
                aria-hidden="true"
              >
                {#each path.keys as key (key)}
                  <span
                    style:inline-size="{((folded.find(
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
          class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
          onclick={() => (isAllPaths = !isAllPaths)}
          >{isAllPaths
            ? m.seats_paths_fewer()
            : m.seats_paths_more({ count: count(paths.length) })}</button
        >
      {/if}
    </div>
  {/if}
  {#if chamber != null}
    <a
      class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
      href={resolve("/id/[id]", { id: chamber })}>{m.chamber_open()}</a
    >
  {/if}
</section>
