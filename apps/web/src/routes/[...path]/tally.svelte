<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import Name from "#lib/components/name.svelte";
  import Term from "#lib/components/term.svelte";
  import { count as figure, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    count,
    here,
  }: {
    count: NonNullable<
      Extract<PageData, { kind: "contest" }>["contest"]["count"]
    >;
    here: string;
  } = $props();

  const uid = $props.id();
  const rounds = $derived(
    Array.from({ length: count.last }, (_, step) => step + 1),
  );
  const totals = $derived(
    rounds.map((round) => {
      let total = 0;
      for (const row of count.rows) {
        total += row.votes[round - 1] ?? 0;
      }
      return total;
    }),
  );
  const href = (round: number) => {
    const kept = [...page.url.searchParams].filter(([key]) => key !== "round");
    const query = new URLSearchParams(
      round === count.initial ? kept : [...kept, ["round", String(round)]],
    );
    const text = query.toString().replaceAll("%2F", "/");
    const suffix = text === "" ? ("" as const) : (`?${text}` as const);
    return resolve(`/[...path]${suffix}`, { path: here });
  };
</script>

<section
  class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0"
  aria-labelledby="{uid}-title"
>
  <div class="grid gap-0.5">
    <h2 id="{uid}-title" class="text-lg font-bold">{m.count_table()}</h2>
    <p class="text-[0.8125rem] text-muted-foreground">
      {m.count_table_note()}
    </p>
  </div>
  <div
    class="overflow-x-auto"
    {@attach (node: HTMLElement) => {
      const cell = node.querySelector<HTMLElement>(
        `thead th:nth-child(${count.round + 1})`,
      );
      if (cell != null) {
        node.scrollLeft = Math.max(
          0,
          cell.offsetLeft + cell.offsetWidth - node.clientWidth,
        );
      }
    }}
  >
    <table class="border-collapse text-sm inline-full">
      <thead>
        <tr class="border-be text-[0.8125rem] text-muted-foreground">
          <th
            class="sticky inset-s-0 z-10 bg-card pe-3 pbe-1.5 text-start font-normal"
            scope="col"
          >
            {m.col_candidate()}
          </th>
          {#each rounds as round (round)}
            <th
              class={cn("px-2 pbe-1.5 text-end font-normal min-inline-16", {
                "bg-secondary font-semibold text-foreground":
                  round === count.round,
              })}
              scope="col"
            >
              <a
                class="font-figures tabular-nums hover:text-foreground"
                href={href(round)}
                aria-current={round === count.round ? "step" : undefined}
                data-sveltekit-reset="false"
                >{round === count.last
                  ? m.count_final()
                  : m.count_round({ round })}</a
              >
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each count.rows as row (row.key)}
          {const reach = $derived(
            row.votes.findLastIndex((value) => value != null) + 1,
          )}
          <tr class="border-be" style:--party={swatch(row.color, row.key)}>
            <th
              class="sticky inset-s-0 z-10 bg-card py-1.5 pe-3 text-start font-normal min-inline-40"
              scope="row"
            >
              <span class="grid">
                <Name
                  class={cn("truncate max-inline-56", {
                    "font-semibold": reach === count.last,
                  })}
                  label={row.label}
                  color={swatch(row.color, row.key)}
                  person={row.person}
                  party={row.party}
                />
                {#if reach < count.last}
                  <span
                    class="ps-4 text-xs whitespace-nowrap text-muted-foreground"
                    >{m.count_out({ round: reach })}</span
                  >
                {/if}
              </span>
            </th>
            {#each rounds as round (round)}
              {const votes = $derived(row.votes[round - 1])}
              {const share = $derived(
                votes == null || totals[round - 1] === 0
                  ? null
                  : votes / (totals[round - 1] ?? 1),
              )}
              <td
                class={cn("px-2 py-1.5 align-top", {
                  "bg-secondary": round === count.round,
                })}
              >
                {#if share != null}
                  <span class="grid justify-items-end gap-1">
                    <span class="grid justify-items-end">
                      <span class="font-figures tabular-nums"
                        >{percent(share)}</span
                      >
                      <span
                        class="font-figures text-xs text-muted-foreground tabular-nums"
                        >{figure(votes ?? 0)}</span
                      >
                    </span>
                    <span
                      class="bg-(--party) block-0.75"
                      style:inline-size="{share * 100}%"
                      aria-hidden="true"
                    ></span>
                  </span>
                {/if}
              </td>
            {/each}
          </tr>
        {/each}
        <tr class="text-[0.8125rem] text-muted-foreground">
          <th
            class="sticky inset-s-0 z-10 bg-card py-1.5 pe-3 text-start font-normal"
            scope="row"
          >
            <Term label={m.count_exhausted()} definition={m.term_exhausted()} />
          </th>
          {#each count.exhausted as spent, index (index)}
            <td
              class={cn("px-2 py-1.5 text-end font-figures tabular-nums", {
                "bg-secondary": index + 1 === count.round,
              })}
            >
              {spent == null ? "" : figure(spent)}
            </td>
          {/each}
        </tr>
      </tbody>
    </table>
  </div>
</section>
