<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
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
  } = $props();

  const filled = $derived.by(() => {
    let total = 0;
    for (const party of parties) {
      total += party.seats;
    }
    return total;
  });
  const isPart = $derived(body != null && size != null && filled < size);

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
    const term = data.makeup
      .filter((entry) => entry.start >= date && entry.start <= until)
      .toSorted((a, b) => a.start.localeCompare(b.start))
      .at(-1);
    if (term == null) {
      return null;
    }
    let total = 0;
    for (const entry of term.parties) {
      total += entry.seats;
    }
    if (total <= filled) {
      return null;
    }
    const info = new Map(data.parties.map((party) => [party.id, party]));
    const local = new Map<string, number>();
    const held = new Map<string, (typeof parties)[number]["members"]>();
    for (const party of parties) {
      const id = /^party:(.+)$/.exec(party.key)?.[1] ?? null;
      const key =
        id != null && term.parties.some((entry) => entry.party === id)
          ? id
          : "none";
      local.set(key, (local.get(key) ?? 0) + party.seats);
      held.set(key, [...(held.get(key) ?? []), ...party.members]);
    }
    return term.parties.map((entry) => {
      const key = entry.party ?? "none";
      const party = entry.party == null ? null : info.get(entry.party);
      return {
        key,
        label:
          party?.label ??
          (entry.isIndependent ? m.seats_independents() : m.record_none()),
        short: party?.short ?? null,
        color: party?.color ?? null,
        lean: party?.lean ?? null,
        seats: entry.seats,
        here: Math.min(entry.seats, local.get(key) ?? 0),
        members: region == null ? (held.get(key) ?? []) : [],
      };
    });
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
