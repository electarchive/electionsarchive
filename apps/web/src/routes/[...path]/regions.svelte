<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const { id }: { id: string } = $props();

  const uid = $props.id();

  const regions = createQuery(() =>
    orpc.elections.regions.queryOptions({ input: { id } }),
  );

  let sorted = $state<string | null>(null);
  let isAll = $state(false);

  const chambers = $derived(
    (regions.data ?? []).map((chamber) => {
      const info = new Map(chamber.parties.map((party) => [party.id, party]));
      const totals = Map.groupBy(
        chamber.regions.flatMap((region) => region.parties),
        (entry) => entry.party,
      )
        .entries()
        .map(([party, entries]) => {
          let seats = 0;
          for (const entry of entries) {
            seats += entry.seats;
          }
          return { party, seats };
        })
        .toArray()
        .toSorted((a, b) => b.seats - a.seats);
      const shown = totals.slice(0, totals.length > 9 ? 8 : 9);
      const columns = shown
        .map((entry) => ({
          key: entry.party ?? "",
          party: entry.party,
          label:
            entry.party == null
              ? m.record_none()
              : (info.get(entry.party)?.short ??
                info.get(entry.party)?.label ??
                ""),
          color:
            entry.party == null
              ? swatch(null, "")
              : swatch(
                  info.get(entry.party)?.color ?? null,
                  `party:${entry.party}`,
                ),
          lean:
            (entry.party == null ? null : info.get(entry.party)?.lean) ??
            Infinity,
        }))
        .toSorted((a, b) => a.lean - b.lean);
      const isFolded = totals.length > columns.length;
      const rows = chamber.regions.map((region) => {
        const cells = columns.map(
          (column) =>
            region.parties.find((entry) => entry.party === column.party)
              ?.seats ?? 0,
        );
        let listed = 0;
        for (const cell of cells) {
          listed += cell;
        }
        return {
          name: region.name,
          seats: region.seats,
          cells,
          others: region.seats - listed,
        };
      });
      const total = columns.map(
        (column) =>
          totals.find((entry) => entry.party === column.party)?.seats ?? 0,
      );
      let all = 0;
      for (const entry of totals) {
        all += entry.seats;
      }
      let listed = 0;
      for (const value of total) {
        listed += value;
      }
      return {
        name: chamber.name,
        noun: chamber.noun,
        columns,
        isFolded,
        rows,
        total,
        others: all - listed,
        all,
      };
    }),
  );
</script>

{#each chambers as chamber, at (chamber.name)}
  {const index = $derived(
    sorted == null
      ? -1
      : chamber.columns.findIndex((column) => column.key === sorted),
  )}
  {const ordered = $derived(
    index === -1
      ? chamber.rows
      : chamber.rows.toSorted(
          (a, b) =>
            (b.cells[index] ?? 0) / Math.max(1, b.seats) -
              (a.cells[index] ?? 0) / Math.max(1, a.seats) ||
            (b.cells[index] ?? 0) - (a.cells[index] ?? 0),
        ),
  )}
  <section
    class="col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="{uid}-{at}"
  >
    <div class="grid gap-0.5">
      <h2
        id="{uid}-{at}"
        class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
      >
        {m.regions_title({ name: chamber.name, noun: chamber.noun ?? "" })}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
        {m.regions_lede({ noun: chamber.noun ?? "" })}
      </p>
    </div>
    <div class="overflow-x-auto">
      <table class="border-collapse text-sm tabular-nums inline-full">
        <thead>
          <tr class="border-be">
            <th
              scope="col"
              class="sticky inset-s-0 bg-card py-1.5 pe-3 text-start text-xs font-semibold text-muted-foreground first-letter:uppercase"
            >
              {chamber.noun ?? m.regions_place()}
            </th>
            {#each chamber.columns as column (column.key)}
              <th scope="col" class="px-0.5 py-1.5 text-end">
                <button
                  type="button"
                  class={cn(
                    "inline-flex items-center gap-1.5 rounded-xs px-1.5 text-xs font-semibold whitespace-nowrap block-7 hover:bg-secondary",
                    {
                      "bg-secondary ring-1 ring-foreground":
                        sorted === column.key,
                    },
                  )}
                  aria-pressed={sorted === column.key}
                  onclick={() =>
                    (sorted = sorted === column.key ? null : column.key)}
                >
                  <span
                    class="shrink-0 rounded-xs block-2 inline-2"
                    style:background={column.color}
                  ></span>
                  {column.label}
                </button>
              </th>
            {/each}
            {#if chamber.isFolded}
              <th
                scope="col"
                class="px-2 py-1.5 text-end text-xs font-semibold text-muted-foreground"
              >
                {m.regions_others()}
              </th>
            {/if}
            <th
              scope="col"
              class="py-1.5 ps-2 text-end text-xs font-semibold text-muted-foreground"
            >
              {m.regions_total()}
            </th>
          </tr>
        </thead>
        <tbody>
          {#each isAll || ordered.length <= 16 ? ordered : ordered.slice(0, 15) as row (row.name)}
            <tr class="border-be border-border/60">
              <th
                scope="row"
                class="sticky inset-s-0 bg-card py-1 pe-3 text-start font-normal whitespace-nowrap"
              >
                {row.name}
              </th>
              {#each row.cells as cell, column (column)}
                {const share = $derived(cell / Math.max(1, row.seats))}
                {const fill = $derived(
                  `color-mix(in oklab, ${chamber.columns[column]?.color} ${Math.round(12 + share * 58)}%, var(--card))`,
                )}
                <td class="p-0.5">
                  <span
                    class={cn(
                      "grid place-items-end rounded-xs px-2 font-mono text-[0.8125rem] block-7",
                      {
                        "text-muted-foreground/50": cell === 0,
                        "font-semibold": share >= 0.5,
                      },
                    )}
                    style:background={cell === 0 ? undefined : fill}
                    style:color={cell === 0
                      ? undefined
                      : `oklch(from ${fill} clamp(0.12, (0.6 - l) * 1000, 0.99) 0 0)`}
                    >{cell === 0 ? "·" : count(cell)}</span
                  >
                </td>
              {/each}
              {#if chamber.isFolded}
                <td
                  class="px-2 py-1 text-end font-mono text-[0.8125rem] text-muted-foreground"
                  >{row.others === 0 ? "·" : count(row.others)}</td
                >
              {/if}
              <td
                class="py-1 ps-2 text-end font-mono text-[0.8125rem] font-semibold"
                >{count(row.seats)}</td
              >
            </tr>
          {/each}
        </tbody>
        <tfoot>
          <tr class="border-bs-2 border-foreground">
            <th
              scope="row"
              class="sticky inset-s-0 bg-card py-1.5 pe-3 text-start font-semibold"
            >
              {m.regions_total()}
            </th>
            {#each chamber.total as value, column (column)}
              <td
                class="px-2 py-1.5 text-end font-mono text-[0.8125rem] font-semibold"
                >{count(value)}</td
              >
            {/each}
            {#if chamber.isFolded}
              <td
                class="px-2 py-1.5 text-end font-mono text-[0.8125rem] font-semibold"
                >{count(chamber.others)}</td
              >
            {/if}
            <td
              class="py-1.5 ps-2 text-end font-mono text-[0.8125rem] font-semibold"
              >{count(chamber.all)}</td
            >
          </tr>
        </tfoot>
      </table>
    </div>
    {#if ordered.length > 16}
      <button
        type="button"
        class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
        onclick={() => (isAll = !isAll)}
        >{isAll
          ? m.regions_fewer()
          : m.regions_more({ count: count(ordered.length) })}</button
      >
    {/if}
  </section>
{/each}
