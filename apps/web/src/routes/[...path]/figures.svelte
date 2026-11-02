<script lang="ts">
  import type { Caveat } from "@ea/analytics/indices";
  import { createQuery } from "@tanstack/svelte-query";
  import type { Figure } from "#lib/indices.js";
  import { show } from "#lib/explain.svelte.js";
  import { count } from "#lib/format.js";
  import { figure } from "#lib/indices.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";

  const { id, body }: { id: string; body: string | null } = $props();

  const election = createQuery(() => ({
    ...orpc.indices.election.queryOptions({ input: { id } }),
    enabled: body != null,
  }));
  const contest = createQuery(() => ({
    ...orpc.indices.contest.queryOptions({ input: { id } }),
    enabled: body == null,
  }));

  const chamber = $derived(
    body == null
      ? (contest.data?.chamber ?? null)
      : (election.data?.chambers.find((entry) => entry.body === body) ?? null),
  );

  const items = $derived.by(() => {
    if (chamber == null) {
      return [];
    }
    const basis = m.indices_basis({
      votes: count(chamber.basis.votes),
      seats: count(chamber.basis.seats),
    });
    const caveats = (...found: Array<{ caveats: Array<Caveat> } | null>) => [
      ...new Set([
        ...chamber.caveats,
        ...found.flatMap((entry) => entry?.caveats ?? []),
      ]),
    ];
    const rows: Array<{
      figure: Figure;
      label: string;
      title: string;
      values: Array<{ text: string; unit: string | null }>;
      notes: Array<string>;
      caveats: Array<Caveat>;
    }> = [];
    const { effectiveVotes, effectiveSeats, gallagher, volatility } = chamber;
    if (effectiveVotes != null && effectiveSeats != null) {
      rows.push({
        figure: "effectiveVotes",
        label: m.indices_effective(),
        title: m.indices_effective(),
        values: [
          {
            text: figure("effectiveVotes", effectiveVotes.value),
            unit: m.indices_by_votes(),
          },
          {
            text: figure("effectiveSeats", effectiveSeats.value),
            unit: m.indices_by_seats(),
          },
        ],
        notes: [basis],
        caveats: caveats(effectiveVotes, effectiveSeats),
      });
    }
    if (gallagher != null) {
      rows.push({
        figure: "gallagher",
        label: m.indices_gallagher(),
        title: m.indices_gallagher_name(),
        values: [{ text: figure("gallagher", gallagher.value), unit: null }],
        notes: [basis],
        caveats: caveats(gallagher),
      });
    }
    if (volatility != null) {
      rows.push({
        figure: "volatility",
        label: m.indices_volatility_since({ year: volatility.previous.year }),
        title: m.indices_volatility(),
        values: [{ text: figure("volatility", volatility.value), unit: null }],
        notes: [m.indices_compared({ election: volatility.previous.name })],
        caveats: caveats(volatility),
      });
    }
    return rows;
  });
</script>

{#if chamber != null && items.length > 0}
  <dl
    class="mbs-3 flex flex-wrap gap-x-5 gap-y-1 border-bs border-border px-3.5 pbs-2.5 text-[0.8125rem]"
    aria-label={m.indices_row({ chamber: chamber.name })}
  >
    {#each items as item (item.figure)}
      <div class="flex items-baseline gap-x-1.5">
        <dt>
          <button
            type="button"
            class="cursor-help text-start text-muted-foreground underline decoration-current/35 decoration-dotted decoration-1 underline-offset-[0.25em] hover:text-foreground hover:decoration-current"
            aria-haspopup="dialog"
            aria-label={m.indices_open({ name: item.title })}
            onclick={(event) =>
              show(event, {
                kind: "index",
                figure: item.figure,
                title: item.title,
                context: chamber.name,
                values: item.values,
                notes: item.notes,
                caveats: item.caveats,
              })}>{item.label}</button
          >
        </dt>
        {#each item.values as value (value.unit)}
          <dd class="font-medium tabular-nums">
            {value.text}{#if value.unit != null}{" "}<span
                class="font-normal text-muted-foreground">{value.unit}</span
              >{/if}
          </dd>
        {/each}
      </div>
    {/each}
  </dl>
{/if}
