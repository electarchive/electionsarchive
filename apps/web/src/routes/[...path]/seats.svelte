<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import { untrack } from "svelte";
  import { on } from "svelte/events";
  import { arrange, isUnplaced } from "#lib/caucus.js";
  import Export from "#lib/components/export.svelte";
  import Name from "#lib/components/name.svelte";
  import { count, natural, percent } from "#lib/format.js";
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
    body = chamber,
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
      isStriped?: boolean;
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
    body?: string | null;
    title?: string | null;
    isLinked?: boolean;
    isUp?: boolean;
    isCompact?: boolean;
  } = $props();

  const uid = $props.id();
  const S = 100;
  const BENCHED = new Set([
    "01a13b8d-56e9-747a-a0a6-f9741f70db7e",
    "01a13fb6-eab8-71a8-b3f0-ca174fba58db",
    "fc04f6de-b056-5266-86b1-6ddf28de75d8",
    "3a07465a-46f6-5fd4-b193-7da4b137e058",
  ]);

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
                        natural(order(a), order(b)) ||
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
  let opened = $state<Array<string>>([]);
  const prefix = $derived.by(() => {
    const first = places[0]?.name ?? "";
    const head = first.slice(0, first.lastIndexOf(", ") + 2);
    return head.length > 2 &&
      places.every((entry) => entry.name.startsWith(head))
      ? head
      : "";
  });
  const isListed = $derived(
    link == null &&
      !isCompact &&
      folded.some((party) =>
        party.members.some((member) => member.name != null),
      ),
  );
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

  const unplaced = $derived(folded.filter(isUnplaced));
  const gone = $derived(
    link == null && moved != null
      ? given.flatMap((party) => {
          const delta = moved[party.key];
          return party.seats === 0 && delta != null && delta < 0
            ? [{ ...party, keys: [party.key], delta }]
            : [];
        })
      : [],
  );
  const ordered = $derived(arrange(folded));
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
  const isBenched = $derived(body != null && BENCHED.has(body));
  const speaker = $derived(
    isBenched
      ? (folded.find((party) => /^speaker$/iu.test(party.label.trim())) ?? null)
      : null,
  );
  const front = $derived.by(() => {
    const sitting = folded.filter(
      (party) => party !== speaker && !party.key.startsWith("category:"),
    );
    const named = sitting.filter((party) => governing.includes(party.key));
    if (named.length > 0) {
      return new Set(named.map((party) => party.key));
    }
    const lead = sitting.toSorted((a, b) => b.seats - a.seats)[0];
    return new Set(
      lead == null
        ? []
        : (partners.find((pair) => pair.includes(lead.key)) ?? [lead.key]),
    );
  });
  const sequence = $derived.by(() => {
    if (!isBenched) {
      return ordered;
    }
    const sized = ordered.toSorted(
      (a, b) =>
        Number(a.key.startsWith("category:")) -
          Number(b.key.startsWith("category:")) || b.seats - a.seats,
    );
    return [
      ...sized.filter((party) => party === speaker),
      ...sized.filter((party) => front.has(party.key)),
      ...sized.filter((party) => party !== speaker && !front.has(party.key)),
    ];
  });
  const bench = $derived.by(() => {
    const chair = speaker?.seats ?? 0;
    let ruling = 0;
    for (const party of folded) {
      if (front.has(party.key)) {
        ruling += party.seats;
      }
    }
    const facing = Math.max(0, totals.all - chair - ruling);
    const aisle = chair > 0 ? 1.6 : 0;
    let best = { columns: 1, near: ruling, far: facing, floor: 3 };
    let closest = Infinity;
    for (let columns = 4; columns <= 90; columns++) {
      const near = Math.ceil(ruling / columns);
      const far = Math.ceil(facing / columns);
      const floor = Math.max(4, Math.round(0.42 * (near + far)));
      const miss = Math.abs(
        Math.log((columns + aisle) / (near + far + floor) / 2.4),
      );
      if (miss < closest) {
        closest = miss;
        best = { columns, near, far, floor };
      }
    }
    const pitch = (2 * S) / (best.columns + aisle);
    return { ...best, chair, ruling, pitch, start: -S + aisle * pitch };
  });
  const dot = $derived(
    Math.round(
      (isBenched ? 0.42 * bench.pitch : 0.84 * layout.thick * S) * 1000,
    ) / 1000,
  );
  const ring = $derived(Math.max(0.6, 0.22 * dot));
  const striped = $derived(
    new Set(
      folded.flatMap((party) => (party.isStriped === true ? [party.key] : [])),
    ),
  );

  const spots = $derived.by(() => {
    const found: Array<{ x: number; y: number }> = [];
    if (isBenched) {
      const { chair, ruling, near, far, floor, pitch, start } = bench;
      for (let seat = 0; seat < chair; seat++) {
        found.push({
          x: start - 0.9 * pitch,
          y: (seat - (chair - 1) / 2) * pitch,
        });
      }
      for (let seat = 0; seat < ruling; seat++) {
        found.push({
          x: start + (Math.floor(seat / near) + 0.5) * pitch,
          y: (floor / 2 + (seat % near) + 0.5) * pitch,
        });
      }
      for (let seat = 0; seat < totals.all - chair - ruling; seat++) {
        found.push({
          x: start + (Math.floor(seat / far) + 0.5) * pitch,
          y: -(floor / 2 + (seat % far) + 0.5) * pitch,
        });
      }
      return found;
    }
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
    for (const position of positions) {
      found.push({
        x: position.radius * S * Math.cos(position.angle),
        y: -position.radius * S * Math.sin(position.angle),
      });
    }
    return found;
  });

  const seats = $derived.by(() => {
    const placed = spots.map((spot, index) => ({
      x: Math.round(spot.x * 100) / 100,
      y: Math.round(spot.y * 100) / 100,
      party: "",
      local: false,
      index,
      member: null as Member | null,
    }));
    let start = 0;
    for (const party of sequence) {
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
        return [
          party.key,
          swatch(
            head.color,
            /^[0-9a-f]{8}-/.test(head.key) ? `party:${head.key}` : head.key,
          ),
        ];
      }),
    ),
  );
  const tones = $derived(
    new Map(folded.map((party, at) => [party.key, `var(--seat-${at})`])),
  );
  const palette = $derived(
    folded
      .map((party, at) => `--seat-${at}: ${colors.get(party.key)}`)
      .join("; "),
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
  const outer = $derived((layout.radii.at(-1) ?? 1) * S + dot);
  const mark = $derived.by(() => {
    if (!whole || totals.all <= 1) {
      return null;
    }
    if (isBenched) {
      const { ruling, near, floor, pitch, start } = bench;
      if (ruling < majority || near === 0) {
        return null;
      }
      const x = start + (Math.floor((majority - 1) / near) + 0.5) * pitch;
      const y = (floor / 2 + near) * pitch + 2;
      return { x1: x, y1: y, x2: x, y2: y + 6 };
    }
    const before = seats[majority - 2];
    const after = seats[majority - 1];
    const angle =
      before == null || after == null
        ? Math.PI / 2
        : (Math.atan2(-before.y, before.x) + Math.atan2(-after.y, after.x)) / 2;
    return {
      x1: (outer + 2) * Math.cos(angle),
      y1: -(outer + 2) * Math.sin(angle),
      x2: (outer + 8) * Math.cos(angle),
      y2: -(outer + 8) * Math.sin(angle),
    };
  });
  const box = $derived.by(() => {
    if (isBenched) {
      const { near, far, floor, pitch } = bench;
      const top = -(floor / 2 + far) * pitch - 1;
      return {
        x: -S - 1,
        y: top,
        width: 2 * S + 2,
        height: (floor / 2 + near) * pitch + (mark == null ? 1 : 9) - top,
      };
    }
    return {
      x: -outer - 1,
      y: -outer - (whole ? 10 : 1),
      width: 2 * outer + 2,
      height: outer + (whole ? 10 : 1) + 1,
    };
  });
  const center = $derived(
    link == null
      ? held > 0
        ? { value: totals.all - held, of: totals.all }
        : { value: totals.all, of: whole ? null : size }
      : { value: totals.local, of: totals.all },
  );
  const label = $derived.by(() => {
    const digits = count(center.value).length;
    if (isBenched) {
      const { columns, floor, pitch, start } = bench;
      const font = Math.max(
        6,
        Math.min(
          (center.of == null ? 0.7 : 0.56) * floor * pitch,
          (0.6 * columns * pitch) / (0.58 * digits + 0.2),
        ),
      );
      return {
        font,
        x: start + (columns * pitch) / 2,
        y: center.of == null ? 0.36 * font : 0.2 * font,
        sub: 0.54 * font,
      };
    }
    const hole = (layout.radii[0] ?? 0.5) * S - dot - 1.5;
    const font = Math.max(
      6,
      Math.min(
        0.66 * hole,
        (1.7 * hole) / (0.58 * digits + 0.2),
        center.of == null ? Infinity : 0.62 * hole,
      ),
    );
    return {
      font,
      x: 0,
      y: center.of == null ? -0.5 : -0.42 * font - 1,
      sub: -0.5,
    };
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
    const reach = isBenched ? 0.75 * bench.pitch : dot + layout.thick * S;
    let best = { index: -1, distance: reach ** 2 };
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
      style:max-inline-size="{isBenched
        ? Math.min(30, 12 + 0.45 * bench.columns)
        : Math.min(26, 11 + 2.4 * layout.rows)}rem"
    >
      <svg
        {@attach (svg: SVGSVGElement) =>
          on(window, "pointerdown", (event) => {
            if (!(event.target instanceof Node && svg.contains(event.target))) {
              active = null;
            }
          })}
        class={cn(
          "block touch-pan-y overflow-visible rounded-sm outline-none inline-full focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring [&>circle]:animate-seat [&>circle]:transition-[fill] [&>circle]:duration-150 motion-reduce:[&>circle]:animate-none motion-reduce:[&>circle]:transition-none",
          { "cursor-pointer": tip?.path != null },
          { "pointer-events-none": isCompact },
        )}
        viewBox="{box.x} {box.y} {box.width} {box.height}"
        style={palette}
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
        {#if isBenched}
          <g class="pointer-events-none stroke-border" aria-hidden="true">
            {#each [-1, 1] as side (side)}
              <line
                stroke-width="0.8"
                x1={bench.start}
                x2={bench.start + bench.columns * bench.pitch}
                y1={side * (bench.floor / 2 - 0.9) * bench.pitch}
                y2={side * (bench.floor / 2 - 0.9) * bench.pitch}
              />
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
            id={active === seat.index ? `${uid}-seat-${seat.index}` : undefined}
            class={{ "stroke-foreground": link != null && seat.local }}
            style:animation-delay="{Math.round(
              (seat.index / seats.length) * 640,
            )}ms"
            cx={seat.x}
            cy={seat.y}
            role="option"
            aria-selected={active === seat.index || undefined}
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
              : seat.index < bench.chair && isBenched
                ? 1.3 * dot
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
                  ? tones.get(seat.party)
                  : `color-mix(in oklab, ${tones.get(seat.party)} 28%, var(--card))`}
          />
        {/each}
        {#if striped.size > 0}
          <g class="pointer-events-none" aria-hidden="true">
            <defs>
              <pattern
                id="{uid}-stripe"
                width={0.7 * dot}
                height={0.7 * dot}
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <rect width={0.24 * dot} height={0.7 * dot} class="fill-card" />
              </pattern>
            </defs>
            {#each seats.filter( (seat) => striped.has(seat.party) ) as seat (seat.index)}
              <circle
                cx={seat.x}
                cy={seat.y}
                r={dot}
                fill="url(#{uid}-stripe)"
                opacity="0.6"
              />
            {/each}
          </g>
        {/if}
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
        {#if mark != null}
          <line
            class="stroke-foreground"
            stroke-width="1.2"
            stroke-linecap="round"
            {...mark}
          />
        {/if}
        <text
          class="pointer-events-none fill-foreground font-figures font-[780] tabular-nums"
          style:font-size="{label.font}px"
          x={label.x}
          y={label.y}
          text-anchor="middle">{count(center.value)}</text
        >
        {#if center.of != null}
          <text
            class="pointer-events-none fill-muted-foreground font-figures tabular-nums"
            style:font-size="{0.3 * label.font}px"
            x={label.x}
            y={label.sub}
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
      {#if isListed}
        <span class="inline-6"></span>
      {/if}
    </div>
  {/if}
  <ul class="grid grid-cols-[minmax(0,1fr)] gap-px text-sm/tight">
    {#each [...ordered.toSorted((a, b) => b.seats - a.seats), ...gone] as party (party.key)}
      {const id = $derived(
        /^(?:party:)?([0-9a-f]{8}-[0-9a-f-]{27})$/.exec(party.key)?.[1] ?? null,
      )}
      {const isMuted = $derived(
        (link != null && party.here === 0) || party.seats === 0,
      )}
      {const color = $derived(
        colors.get(party.key) ??
          swatch(
            party.color,
            /^[0-9a-f]{8}-/.test(party.key) ? `party:${party.key}` : party.key,
          ),
      )}
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
        {#if whole && folded.length > 1 && !isCompact && party.seats > 0}
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
          class={cn(
            "shrink-0 rounded-xs block-2.5 inline-2.5",
            { "opacity-35": isMuted },
            {
              "bg-[repeating-linear-gradient(45deg,transparent_0_2px,var(--card)_2px_3px)]":
                party.isStriped === true,
            },
          )}
          style:background-color={color}
        ></span>
        <span class="flex-1 truncate font-semibold min-inline-0">
          <Name
            class="relative z-1 -my-1 inline-block truncate py-1 align-bottom max-inline-full min-inline-6"
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
          {#if isListed}
            {#if party.members.some((member) => member.name != null)}
              <button
                type="button"
                class="relative z-1 -my-1 grid shrink-0 place-items-center rounded-xs text-muted-foreground block-6 inline-6 hover:bg-secondary hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none pointer-coarse:block-8"
                aria-expanded={opened.includes(party.key)}
                aria-controls="{uid}-members-{party.key}"
                aria-label={m.seats_members_toggle({
                  party: party.short ?? party.label,
                })}
                onclick={() =>
                  (opened = opened.includes(party.key)
                    ? opened.filter((key) => key !== party.key)
                    : [...opened, party.key])}
              >
                <ChevronDownIcon
                  class={cn(
                    "transition-transform duration-150 block-4 inline-4 motion-reduce:transition-none",
                    { "rotate-180": opened.includes(party.key) },
                  )}
                  aria-hidden="true"
                />
              </button>
            {:else}
              <span class="shrink-0 inline-6"></span>
            {/if}
          {/if}
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
      {#if isListed && opened.includes(party.key)}
        {const roster = $derived.by(() => {
          const groups = new Map<number, Array<Member>>();
          for (const member of party.members) {
            if (member.name != null) {
              groups.set(member.place, [
                ...(groups.get(member.place) ?? []),
                member,
              ]);
            }
          }
          return [...groups]
            .map(([place, members]) => ({
              seat: places.length < 2 ? null : (places[place] ?? null),
              members: members.toSorted(
                (a, b) => (a.position ?? Infinity) - (b.position ?? Infinity),
              ),
            }))
            .toSorted((a, b) =>
              natural(a.seat?.name ?? "", b.seat?.name ?? ""),
            );
        })}
        <li id="{uid}-members-{party.key}" class="pbe-1.5">
          <div
            class="overflow-hidden rounded-xs border border-border bg-secondary/60 shadow-[inset_2px_0_0_var(--party)]"
            style:--party={color}
          >
            <div
              class="scrollbar-thin [scrollbar-color:var(--input)_transparent] overflow-y-auto overscroll-y-contain py-1 ps-3.5 pe-2.5 max-block-[min(22rem,60dvh)]"
              {@attach (node: HTMLElement) => {
                const fade = () => {
                  const top = Math.min(node.scrollTop, 24);
                  const bottom = Math.min(
                    node.scrollHeight - node.clientHeight - node.scrollTop,
                    24,
                  );
                  node.style.maskImage = `linear-gradient(to bottom, transparent, #000 ${top}px, #000 calc(100% - ${bottom}px), transparent)`;
                };
                fade();
                return on(node, "scroll", fade, { passive: true });
              }}
            >
              {#if roster.every((group) => group.seat == null)}
                <ul
                  class="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-x-4 py-1 text-[0.8125rem]/snug"
                >
                  {#each roster.flatMap((group) => group.members) as member, index (index)}
                    <li class="truncate py-0.5">
                      <Name
                        label={member.name ?? ""}
                        person={member.person ?? null}
                        hasSwatch={false}
                      />
                    </li>
                  {/each}
                </ul>
              {:else}
                <dl
                  class="grid grid-cols-[minmax(0,max-content)_minmax(0,1fr)] gap-x-4 text-[0.8125rem]/snug"
                >
                  {#each roster as group, index (index)}
                    <div
                      class="col-span-2 grid grid-cols-subgrid items-baseline border-bs border-border/70 py-1 first:border-bs-0"
                    >
                      <dt
                        class="truncate text-xs/snug text-muted-foreground max-inline-36"
                      >
                        {#if group.seat == null}{:else if group.seat.path == null}{group.seat.name.slice(
                            prefix.length,
                          )}{:else}<Preview
                            class="hover:text-foreground hover:underline hover:decoration-border hover:underline-offset-4"
                            path={group.seat.path}
                            >{group.seat.name.slice(prefix.length)}</Preview
                          >{/if}
                      </dt>
                      <dd class="grid">
                        {#each group.members as member, slot (slot)}
                          <span class="truncate"
                            ><Name
                              label={member.name ?? ""}
                              person={member.person ?? null}
                              hasSwatch={false}
                            /></span
                          >
                        {/each}
                      </dd>
                    </div>
                  {/each}
                </dl>
              {/if}
            </div>
          </div>
        </li>
      {/if}
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
        {#if isListed}<span class="shrink-0 inline-6"></span>{/if}
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
        {#if isListed}<span class="shrink-0 inline-6"></span>{/if}
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
        {#if isListed}<span class="shrink-0 inline-6"></span>{/if}
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
