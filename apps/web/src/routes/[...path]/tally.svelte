<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import Name from "#lib/components/name.svelte";
  import Term from "#lib/components/term.svelte";
  import { fits } from "#lib/fits.js";
  import { count as figure, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { reveal } from "#lib/reveal.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import { runs } from "./runs.js";

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
  const cellsOf = (votes: Array<number | null>) =>
    rounds.map((round) => {
      const value = votes[round - 1];
      const total = totals[round - 1] ?? 0;
      const share = value == null || total === 0 ? null : value / total;
      return {
        kind: share == null ? ("blank" as const) : ("live" as const),
        round,
        now: round === count.round ? "" : undefined,
        share: share == null ? "" : percent(share),
        votes: figure(value ?? 0),
        bar: `${((share ?? 0) * 100).toFixed(1)}%`,
      };
    });
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
  id="rounds"
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
    class="overflow-x-auto overflow-y-hidden overscroll-x-contain min-inline-0 not-data-fits:overflow-y-auto not-data-fits:max-block-svh data-fits:overflow-visible"
    {@attach fits}
    {@attach reveal(`thead th:nth-child(${count.round + 1})`)}
  >
    <table class="border-collapse text-sm inline-full">
      <thead
        class="sticky inset-bs-0 z-2 bg-background [&_th]:shadow-[inset_0_-1px_0_var(--color-border)]"
      >
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
      <tbody
        class="**:data-bar:ms-auto **:data-bar:mbs-1 **:data-bar:bg-(--party) **:data-bar:block-0.75 **:data-now:bg-secondary [&_small]:block [&_small]:text-xs [&_small]:text-muted-foreground [&_td]:px-2 [&_td]:py-1.5 [&_td]:text-end [&_td]:align-top [&_td]:font-figures [&_td]:tabular-nums [&_td>span]:block"
      >
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
            {const parts = $derived(runs(cellsOf(row.votes)))}
            {#each parts as part (part.round)}
              {#if part.kind === "live"}
                {#each part.cells as cell (cell.round)}
                  <td data-now={cell.now}>
                    <span>{cell.share}</span>
                    <small>{cell.votes}</small>
                    <span data-bar style:inline-size={cell.bar}></span>
                  </td>
                {/each}
              {:else}
                {#each part.cells as cell (cell.round)}
                  <td data-now={cell.now}></td>
                {/each}
              {/if}
            {/each}
          </tr>
        {/each}
        {#if count.exhausted.some((spent) => spent != null)}
          <tr class="text-[0.8125rem] text-muted-foreground">
            <th
              class="sticky inset-s-0 z-10 bg-card py-1.5 pe-3 text-start font-normal"
              scope="row"
            >
              <Term
                label={m.count_exhausted()}
                definition={m.term_exhausted()}
              />
            </th>
            {#each count.exhausted as spent, index (index)}
              <td class={cn({ "bg-secondary": index + 1 === count.round })}>
                {spent == null ? "" : figure(spent)}
              </td>
            {/each}
          </tr>
        {/if}
      </tbody>
    </table>
  </div>
</section>
