<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const {
    id,
    parts,
    noun,
    race,
  }: {
    id: string;
    parts: Array<{ place: string | null; name: string; path: string | null }>;
    noun: string;
    race: string | null;
  } = $props();

  const uid = $props.id();

  const TILES: Record<string, [number, number]> = {
    ak: [0, 0],
    me: [10, 0],
    vt: [9, 1],
    nh: [10, 1],
    wa: [0, 2],
    id: [1, 2],
    mt: [2, 2],
    nd: [3, 2],
    mn: [4, 2],
    il: [5, 2],
    wi: [6, 2],
    mi: [7, 2],
    ny: [8, 2],
    ri: [9, 2],
    ma: [10, 2],
    or: [0, 3],
    nv: [1, 3],
    wy: [2, 3],
    sd: [3, 3],
    ia: [4, 3],
    in: [5, 3],
    oh: [6, 3],
    pa: [7, 3],
    nj: [8, 3],
    ct: [9, 3],
    ca: [0, 4],
    ut: [1, 4],
    co: [2, 4],
    ne: [3, 4],
    mo: [4, 4],
    ky: [5, 4],
    wv: [6, 4],
    va: [7, 4],
    md: [8, 4],
    de: [9, 4],
    az: [1, 5],
    nm: [2, 5],
    ks: [3, 5],
    ar: [4, 5],
    tn: [5, 5],
    nc: [6, 5],
    sc: [7, 5],
    dc: [8, 5],
    ok: [3, 6],
    la: [4, 6],
    ms: [5, 6],
    al: [6, 6],
    ga: [7, 6],
    hi: [0, 7],
    tx: [3, 7],
    fl: [8, 7],
    pr: [10, 7],
    gu: [0, 8],
    mp: [1, 8],
    as: [2, 8],
    vi: [10, 8],
  };

  const tiers = createQuery(() =>
    orpc.elections.tiers.queryOptions({ input: { id } }),
  );

  const parties = $derived(
    new Map((tiers.data?.parties ?? []).map((party) => [party.id, party])),
  );
  const tint = (party: string | null) =>
    party == null
      ? swatch(null, "")
      : swatch(parties.get(party)?.color ?? null, `party:${party}`);
  const label = (party: string | null) =>
    party == null
      ? m.record_none()
      : (parties.get(party)?.short ?? parties.get(party)?.label ?? "");
  const lean = (party: string | null) =>
    (party == null ? null : parties.get(party)?.lean) ?? Infinity;
  const fill = (color: string, amount: number) =>
    `color-mix(in oklab, ${color} ${Math.round(amount * 100)}%, var(--card))`;

  const known = $derived(
    new Map(
      (tiers.data?.parts ?? []).flatMap((part) =>
        part.path == null ? [] : [[part.path, part] as const],
      ),
    ),
  );
  const places = $derived(
    parts.flatMap((part) => {
      const code = part.path?.split("/")[1] ?? "";
      const at = TILES[code];
      return at == null
        ? []
        : [
            {
              code,
              at,
              place: part.place ?? part.name,
              path: part.path,
              id: known.get(part.path ?? "")?.id ?? null,
              top: known.get(part.path ?? "")?.top ?? null,
            },
          ];
    }),
  );
  const isTopped = $derived(places.some((place) => place.top != null));

  const panels = $derived(
    (tiers.data?.tiers ?? []).map((tier) => {
      const rows = new Map(tier.rows.map((row) => [row.part, row]));
      let races = 0;
      let passed = 0;
      for (const row of tier.rows) {
        races += row.races;
        passed += row.passed;
      }
      const bar = Map.groupBy(
        tier.rows.flatMap((row) =>
          tier.kind === "governor"
            ? row.parties
                .slice(0, 1)
                .map((entry) => ({ party: entry.party, value: 1 }))
            : row.parties.map((entry) => ({
                party: entry.party,
                value: entry.seats,
              })),
        ),
        (entry) => entry.party,
      )
        .entries()
        .map(([party, entries]) => {
          let value = 0;
          for (const entry of entries) {
            value += entry.value;
          }
          return { party, value };
        })
        .toArray()
        .toSorted((a, b) => lean(a.party) - lean(b.party) || b.value - a.value);
      let seats = 0;
      for (const part of bar) {
        seats += part.value;
      }
      return {
        kind: tier.kind,
        title: {
          governor: m.tiers_governor,
          lower: m.tiers_lower,
          upper: m.tiers_upper,
          measure: m.tiers_measure,
        }[tier.kind](),
        tally:
          tier.kind === "measure"
            ? m.tiers_passed({ passed: count(passed), count: count(races) })
            : tier.kind === "governor"
              ? m.tiers_races({ count: count(tier.rows.length) })
              : m.tiers_seats({
                  seats: count(seats),
                  count: count(tier.rows.length),
                }),
        key:
          tier.kind === "measure"
            ? m.tiers_measure_key()
            : tier.kind === "governor"
              ? null
              : m.tiers_chamber_key(),
        bar,
        lead: bar
          .toSorted((a, b) => b.value - a.value)
          .slice(0, 2)
          .toSorted((a, b) => lean(a.party) - lean(b.party)),
        total: seats,
        tiles: places.map((place) => {
          const row = place.id == null ? null : rows.get(place.id);
          if (row == null) {
            return {
              ...place,
              color: null,
              text: m.tiers_tile_none({ place: place.place }),
            };
          }
          if (tier.kind === "measure") {
            return {
              ...place,
              color: fill(
                "var(--yes)",
                0.2 + (0.8 * row.passed) / Math.max(1, row.races),
              ),
              text: m.tiers_tile_measures({
                place: place.place,
                passed: count(row.passed),
                count: count(row.races),
              }),
            };
          }
          const first = row.parties[0];
          let filled = 0;
          for (const entry of row.parties) {
            filled += entry.seats;
          }
          const share = first == null ? 0 : first.seats / Math.max(1, filled);
          return {
            ...place,
            color: fill(
              tint(first?.party ?? null),
              tier.kind === "governor"
                ? 1
                : Math.min(1, Math.max(0.35, (share - 0.4) / 0.35)),
            ),
            text:
              tier.kind === "governor"
                ? m.tiers_tile_party({
                    place: place.place,
                    party: label(first?.party ?? null),
                  })
                : m.tiers_tile_seats({
                    place: place.place,
                    party: label(first?.party ?? null),
                    seats: count(first?.seats ?? 0),
                    total: count(filled),
                  }),
          };
        }),
      };
    }),
  );
</script>

{#snippet map(
  tiles: Array<{
    code: string;
    at: [number, number];
    path: string | null;
    color: string | null;
    text: string;
  }>,
  size: "large" | "small",
)}
  <ol
    class="grid grid-cols-11 gap-0.5 max-inline-136 data-[size=small]:max-inline-80"
    data-size={size}
  >
    {#each tiles as tile (tile.code)}
      <li
        class="aspect-square min-inline-0"
        style:grid-column={tile.at[0] + 1}
        style:grid-row={tile.at[1] + 1}
      >
        <svelte:element
          this={tile.path == null ? "span" : "a"}
          href={tile.path == null
            ? undefined
            : resolve("/[...path]", { path: tile.path })}
          class="grid place-items-center rounded-xs font-figures font-semibold tabular-nums ring-1 ring-border/70 transition-shadow outline-none block-full inline-full ring-inset hover:ring-2 hover:ring-foreground focus-visible:ring-2 focus-visible:ring-ring data-filled:ring-0 data-[size=large]:text-[0.6875rem] data-[size=small]:text-[0.5625rem]"
          style:background={tile.color ?? undefined}
          style:color={tile.color == null
            ? undefined
            : `color(from ${tile.color} xyz-d65 calc(0.9505 * clamp(0, (0.179 - y) * 1e6, 1)) clamp(0, (0.179 - y) * 1e6, 1) calc(1.089 * clamp(0, (0.179 - y) * 1e6, 1)))`}
          data-filled={tile.color == null ? undefined : ""}
          data-size={size}
          title={tile.text}
          aria-label={tile.text}>{tile.code.toUpperCase()}</svelte:element
        >
      </li>
    {/each}
  </ol>
{/snippet}

{#if places.length > 0}
  <section
    id="down-the-ballot"
    class="col-span-12 grid scroll-mbs-4 grid-cols-[minmax(0,1fr)] gap-4 bg-card px-3.5 pbs-3.5 pbe-4"
    aria-labelledby="{uid}-title"
  >
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="heading">
        {panels.length > 0 ? m.tiers_title() : m.election_places({ noun })}
      </h2>
      {#if panels.length > 0}
        <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
          {m.tiers_lede({ noun })}
        </p>
      {/if}
    </div>
    <div
      class={cn("grid grid-cols-[minmax(0,1fr)] gap-x-8 gap-y-6", {
        "lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]": isTopped,
        "sm:grid-cols-2 lg:grid-cols-3": !isTopped,
      })}
    >
      <figure class="grid content-start gap-2 min-inline-0">
        <figcaption class="text-[0.8125rem] font-bold">
          {m.election_places({ noun })}
        </figcaption>
        {@render map(
          places.map((place) => ({
            ...place,
            color: place.top == null ? null : tint(place.top),
            text:
              place.top == null
                ? place.place
                : m.tiers_tile_party({
                    place: place.place,
                    party: label(place.top),
                  }),
          })),
          isTopped ? "large" : "small",
        )}
        {#if isTopped && race != null}
          <p class="text-xs text-muted-foreground">
            {m.tiers_parts_key({ race, noun })}
          </p>
        {/if}
      </figure>
      {#if panels.length > 0}
        <div
          class={cn({
            "grid content-start gap-6 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2":
              isTopped,
            contents: !isTopped,
          })}
        >
          {#each panels as panel (panel.kind)}
            <figure
              class="grid grid-cols-[minmax(0,1fr)] content-start gap-2 min-inline-0"
            >
              <figcaption
                class="flex flex-wrap items-baseline justify-between gap-x-3"
              >
                <span class="text-[0.8125rem] font-bold">{panel.title}</span>
                <span class="font-figures text-xs text-muted-foreground"
                  >{panel.tally}</span
                >
              </figcaption>
              {#if panel.kind !== "measure" && panel.total > 0}
                <div class="grid gap-1">
                  <div class="flex overflow-hidden rounded-xs block-2">
                    {#each panel.bar as part (part.party)}
                      <span
                        style:background={tint(part.party)}
                        style:flex-grow={part.value}
                        title="{label(part.party)} {count(part.value)}"
                      ></span>
                    {/each}
                  </div>
                  <div
                    class="flex justify-between gap-3 font-figures text-xs tabular-nums"
                  >
                    {#each panel.lead as part (part.party)}
                      <span
                        class="flex items-center gap-1.5 truncate min-inline-0"
                      >
                        <span
                          class="shrink-0 rounded-xs block-2 inline-2"
                          style:background={tint(part.party)}
                        ></span>
                        <span class="truncate font-sans"
                          >{label(part.party)}</span
                        >
                        <span class="font-semibold">{count(part.value)}</span>
                      </span>
                    {/each}
                  </div>
                </div>
              {/if}
              {@render map(panel.tiles, "small")}
              {#if panel.key != null}
                <p class="text-xs text-muted-foreground">{panel.key}</p>
              {/if}
            </figure>
          {/each}
        </div>
      {/if}
    </div>
  </section>
{/if}
