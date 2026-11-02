<script lang="ts">
  import type { Caveat } from "@ea/analytics/indices";
  import { createQuery } from "@tanstack/svelte-query";
  import type { Figure } from "#lib/indices.js";
  import { show } from "#lib/explain.svelte.js";
  import { figure, figureLabel } from "#lib/indices.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const { sides }: { sides: Array<{ id: string; name: string }> } = $props();

  const FIGURES = [
    "effectiveVotes",
    "effectiveSeats",
    "gallagher",
    "volatility",
  ] as const;
  const WIDTH = 96;
  const HEIGHT = 24;

  const queries = [0, 1].map((index) =>
    createQuery(() => ({
      ...orpc.indices.polity.queryOptions({
        input: { id: sides[index]?.id ?? "" },
      }),
      enabled: sides[index] != null,
    })),
  );

  const leads = $derived(queries.map((query) => query.data?.series[0] ?? null));
  const isPending = $derived(queries.some((query) => query.isPending));
  const span = $derived.by(() => {
    let first = Infinity;
    let last = -Infinity;
    for (const lead of leads) {
      for (const point of lead?.points ?? []) {
        first = Math.min(first, point.year);
        last = Math.max(last, point.year);
      }
    }
    return { first, last };
  });

  const rows = $derived(
    FIGURES.map((kind) => {
      const floor = kind.startsWith("effective") ? 1 : 0;
      let top = floor + 1;
      for (const lead of leads) {
        for (const point of lead?.points ?? []) {
          top = Math.max(top, point[kind] ?? top);
        }
      }
      const x = (year: number) =>
        span.last === span.first
          ? WIDTH / 2
          : 2 + ((year - span.first) / (span.last - span.first)) * (WIDTH - 4);
      const y = (value: number) =>
        HEIGHT - 2 - ((value - floor) / (top - floor)) * (HEIGHT - 4);
      const cells = leads.map((lead, index) => {
        const latest = lead?.points.at(-1) ?? null;
        let path = "";
        let isOpen = false;
        for (const point of lead?.points ?? []) {
          const value = point[kind];
          if (value == null) {
            isOpen = false;
            continue;
          }
          path += `${isOpen ? "L" : "M"}${x(point.year).toFixed(1)},${y(value).toFixed(1)}`;
          isOpen = true;
        }
        const value = latest?.[kind] ?? null;
        return {
          side: sides[index]?.id ?? String(index),
          latest,
          value,
          text: value == null ? null : figure(kind, value),
          path,
          dot:
            latest == null || value == null
              ? null
              : { x: x(latest.year), y: y(value) },
        };
      });
      return { kind, cells };
    }),
  );

  const open = (
    event: MouseEvent,
    kind: Figure,
    row: (typeof rows)[number],
  ) => {
    const caveats: Array<Caveat> = [];
    for (const cell of row.cells) {
      for (const caveat of cell.latest?.caveats ?? []) {
        if (!caveats.includes(caveat)) {
          caveats.push(caveat);
        }
      }
    }
    show(event, {
      kind: "index",
      figure: kind,
      title: figureLabel(kind),
      context: m.versus_system_both({
        a: sides[0]?.name ?? "",
        b: sides[1]?.name ?? "",
      }),
      values: row.cells.map((cell, index) => ({
        text: cell.text ?? "–",
        unit: sides[index]?.name ?? null,
        value: cell.value,
      })),
      reading: null,
      notes: row.cells.flatMap((cell, index) =>
        cell.latest == null
          ? []
          : [
              m.versus_system_from({
                name: sides[index]?.name ?? "",
                election: cell.latest.name,
              }),
            ],
      ),
      caveats,
    });
  };
</script>

{#snippet marker(side: number)}
  <span
    class={cn("inline-block shrink-0 bg-foreground block-2 inline-2", {
      "rounded-full": side === 0,
    })}
    aria-hidden="true"
  ></span>
{/snippet}

{#if isPending}
  <div
    class="col-span-12 bg-card block-56 motion-safe:animate-pulse"
    role="status"
    aria-label={m.indices_title()}
  ></div>
{:else if leads.some((lead) => lead != null)}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-4 pbe-4"
    aria-labelledby="system"
  >
    <div class="grid gap-0.5">
      <h2 id="system" class="heading">{m.indices_title()}</h2>
      <p class="text-xs text-pretty text-muted-foreground max-inline-[70ch]">
        {m.versus_system_lede({ year: String(span.first) })}
      </p>
    </div>
    <table class="border-collapse text-[0.8125rem] inline-full max-inline-176">
      <thead>
        <tr class="border-be border-border">
          <td></td>
          {#each sides as side, index (side.id)}
            {const lead = $derived(leads[index] ?? null)}
            <th
              scope="col"
              class="ps-3 pbe-2 text-start align-bottom font-normal"
            >
              <span class="flex items-center gap-1.5 font-semibold"
                >{@render marker(index)}<span class="truncate">{side.name}</span
                ></span
              >
              {#if lead != null}
                <span class="block text-xs text-muted-foreground"
                  >{m.versus_system_column({
                    chamber: lead.name,
                    year: String(lead.points.at(-1)?.year ?? ""),
                  })}</span
                >
              {/if}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each rows as row (row.kind)}
          <tr class="border-be border-border last:border-be-0">
            <th
              scope="row"
              class="py-2.5 pe-2 text-start align-top font-normal"
            >
              <button
                type="button"
                class="cursor-help text-start text-muted-foreground underline decoration-current/35 decoration-dotted decoration-1 underline-offset-[0.25em] hover:text-foreground hover:decoration-current"
                aria-haspopup="dialog"
                aria-label={m.indices_open({ name: figureLabel(row.kind) })}
                onclick={(event) => open(event, row.kind, row)}
                >{figureLabel(row.kind)}</button
              >
            </th>
            {#each row.cells as cell (cell.side)}
              <td class="py-2.5 ps-3 align-top">
                <span class="grid justify-items-start gap-1">
                  <span
                    class={cn("text-base/none tabular-nums", {
                      "font-semibold": cell.text != null,
                      "text-muted-foreground": cell.text == null,
                    })}>{cell.text ?? "–"}</span
                  >
                  {#if cell.path !== ""}
                    <svg
                      width={WIDTH}
                      height={HEIGHT}
                      viewBox="0 0 {WIDTH} {HEIGHT}"
                      class="overflow-visible"
                      aria-hidden="true"
                    >
                      <path
                        d={cell.path}
                        class="fill-none stroke-muted-foreground"
                        stroke-width="1.25"
                        stroke-linejoin="round"
                        stroke-linecap="round"
                      />
                      {#if cell.dot != null}
                        <circle
                          cx={cell.dot.x}
                          cy={cell.dot.y}
                          r="2.5"
                          class="fill-foreground"
                        />
                      {/if}
                    </svg>
                  {/if}
                </span>
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </section>
{/if}
