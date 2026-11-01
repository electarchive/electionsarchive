<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { compose } from "#lib/caucus.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import Seats from "./seats.svelte";

  const {
    parties,
    places,
    body,
    size,
    date,
    place,
    region,
    change,
    isComplete,
    partners,
    governing,
    isStaggered = false,
    unelected = [],
  }: {
    parties: Array<{
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      lean: number | null;
      party?: string | null;
      isStriped?: boolean;
      seats: number;
      here: number;
      members: Array<{
        place: number;
        name: string | null;
        position: number | null;
        person?: string | null;
        party?: string | null;
        affiliation?: string | null;
      }>;
    }>;
    places: Array<{ name: string; path: string | null }>;
    body: string | null;
    size: number | null;
    date: string | null;
    place: string;
    region: string | null;
    change: Record<string, number> | null;
    isComplete: boolean;
    partners: Array<Array<string>>;
    governing: Array<string>;
    isStaggered?: boolean;
    unelected?: Array<{ category: "appointed" | "ex_officio"; seats: number }>;
  } = $props();

  const filled = $derived.by(() => {
    let total = 0;
    for (const party of parties) {
      total += party.seats;
    }
    return total;
  });
  const isPart = $derived(
    body != null && size != null && (filled < size || unelected.length > 0),
  );

  const chamber = createQuery(() => ({
    ...orpc.bodies.get.queryOptions({ input: { id: body ?? "" } }),
    enabled: isPart,
  }));

  const whole = $derived.by(() => {
    const data = chamber.data;
    if (!isPart || data == null || date == null) {
      return null;
    }
    const until = new Date(Date.parse(date) + 120 * 86_400_000)
      .toISOString()
      .slice(0, 10);
    const found = data.makeup
      .filter(
        (entry) =>
          entry.start >= date &&
          entry.start <= until &&
          entry.parties.some((party) => party.category === "elected"),
      )
      .toSorted((a, b) => a.start.localeCompare(b.start))
      .at(-1);
    if (found == null && unelected.length === 0) {
      return null;
    }
    const own = parties.map((party) => ({
      id: /^party:(.+)$/.exec(party.key)?.[1] ?? null,
      party,
    }));
    const term = found ?? {
      parties: [
        ...own.map(({ id, party }) => ({
          party: id,
          caucus: party.key.includes(">")
            ? party.key.slice(party.key.indexOf(">") + 1)
            : null,
          category: "elected" as const,
          seats: party.seats,
          isIndependent: id == null,
        })),
        ...unelected.map((entry) => ({
          party: null,
          category: entry.category,
          seats: entry.seats,
          isIndependent: false,
        })),
      ],
    };
    let total = 0;
    for (const entry of term.parties) {
      total += entry.seats;
    }
    if (total <= filled) {
      return null;
    }
    const composed = compose(term.parties, [
      ...data.parties,
      ...own.flatMap(({ id, party }) => (id == null ? [] : [{ ...party, id }])),
    ]);
    const keys = new Set(composed.map((party) => party.key));
    const local = new Map<string, number>();
    const held = new Map<string, (typeof parties)[number]["members"]>();
    for (const party of parties) {
      const id = /^party:(.+)$/.exec(party.key)?.[1] ?? party.key;
      const key = keys.has(id)
        ? id
        : party.party == null && keys.has("independent")
          ? "independent"
          : "none";
      local.set(key, (local.get(key) ?? 0) + party.seats);
      held.set(key, [...(held.get(key) ?? []), ...party.members]);
    }
    return composed.map((party) => ({
      key: party.key,
      label: party.label ?? m.record_none(),
      short: party.short,
      color: party.color,
      lean: party.lean,
      isStriped: party.isStriped,
      seats: party.seats,
      here: Math.min(party.seats, local.get(party.key) ?? 0),
      members: region == null ? (held.get(party.key) ?? []) : [],
    }));
  });
</script>

{#if whole != null}
  <Seats
    parties={whole}
    places={region == null ? places : []}
    here={null}
    place={region ?? place}
    link={{ name: chamber.data?.name ?? place, path: null }}
    {size}
    chamber={body}
    isUp={region == null}
  />
{:else if !isPart || chamber.isError || chamber.data != null}
  <Seats
    {parties}
    {places}
    here={null}
    {place}
    link={null}
    {size}
    held={isStaggered && isPart ? (size ?? 0) - filled : 0}
    {change}
    {isComplete}
    {partners}
    {governing}
    chamber={body}
  />
{/if}
