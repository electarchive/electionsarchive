<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import Name from "#lib/components/name.svelte";
  import Term from "#lib/components/term.svelte";
  import { count as figure } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  type Count = NonNullable<
    Extract<PageData, { kind: "contest" }>["contest"]["count"]
  >;

  const {
    count,
    stv,
    here,
  }: { count: Count; stv: NonNullable<Count["stv"]>; here: string } = $props();

  const uid = $props.id();
  const list = new Intl.ListFormat(getLocale(), {
    style: "long",
    type: "conjunction",
  });
  const rounds = $derived(
    Array.from({ length: count.last }, (_, step) => step + 1),
  );
  const labels = $derived(new Map(count.rows.map((row) => [row.key, row])));
  const actions = $derived(
    stv.steps.map((step) => {
      const names = list.format(
        step.from.map((key) => labels.get(key)?.label ?? ""),
      );
      return step.kind === "first"
        ? m.stv_first()
        : step.kind === "surplus"
          ? m.stv_surplus({ names })
          : step.kind === "exclusion"
            ? m.stv_exclusion({ names })
            : m.stv_none();
    }),
  );
  const rows = $derived(
    count.rows
      .map((row) => {
        const elected = stv.steps.findIndex((step) =>
          step.elected.includes(row.key),
        );
        const out = stv.steps.findIndex(
          (step) => step.kind === "exclusion" && step.from.includes(row.key),
        );
        return {
          ...row,
          elected: elected === -1 ? null : elected + 1,
          out: out === -1 ? null : out + 1,
          isShort:
            elected !== -1 &&
            row.votes.every((value) => (value ?? 0) < stv.quota),
        };
      })
      .toSorted(
        (a, b) =>
          (a.elected ?? Infinity) - (b.elected ?? Infinity) ||
          (b.out ?? Infinity) - (a.out ?? Infinity) ||
          (b.votes.at(-1) ?? 0) - (a.votes.at(-1) ?? 0),
      ),
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
  const signed = (value: number) =>
    `${value > 0 ? "+" : "−"}${figure(Math.abs(value))}`;
</script>

<section
  class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0"
  aria-labelledby="{uid}-title"
>
  <div class="grid gap-1">
    <h2 id="{uid}-title" class="text-lg font-bold">{m.stv_title()}</h2>
    <p
      class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[72ch]"
    >
      {m.stv_lede({ seats: figure(stv.seats), quota: figure(stv.quota) })}
      {#if stv.isDerived}
        {m.stv_quota_derived()}
      {/if}
    </p>
  </div>
  <div
    class="overflow-x-auto"
    {@attach (node: HTMLElement) => {
      const cell = node.querySelector<HTMLElement>(
        `thead th:nth-child(${count.round + 1})`,
      );
      if (cell != null && count.round > 1) {
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
            class="sticky inset-s-0 z-10 bg-card pe-3 pbe-1.5 text-start align-bottom font-normal"
            scope="col"
          >
            {m.col_candidate()}
          </th>
          {#each rounds as round (round)}
            <th
              class={cn(
                "px-2 pbe-1.5 text-end align-bottom font-normal max-inline-36 min-inline-24",
                { "bg-secondary text-foreground": round === count.round },
              )}
              scope="col"
            >
              <a
                class="grid justify-items-end gap-0.5 hover:text-foreground"
                href={href(round)}
                aria-current={round === count.round ? "step" : undefined}
                data-sveltekit-reset="false"
              >
                <span class="font-figures font-semibold tabular-nums"
                  >{m.stv_count({ count: round })}</span
                >
                <span class="text-xs text-balance">{actions[round - 1]}</span>
              </a>
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each rows as row (row.key)}
          <tr class="border-be" style:--party={swatch(row.color, row.key)}>
            <th
              class="sticky inset-s-0 z-10 bg-card py-1.5 pe-3 text-start font-normal max-inline-40 min-inline-36 sm:max-inline-64 sm:min-inline-48"
              scope="row"
            >
              <span class="grid">
                <Name
                  class={cn("truncate max-inline-56", {
                    "font-semibold": row.elected != null,
                  })}
                  label={row.label}
                  color={swatch(row.color, row.key)}
                  person={row.person}
                  party={row.party}
                />
                {#if row.elected != null}
                  <span class="ps-4 text-xs text-pretty"
                    >{row.isShort
                      ? m.stv_elected_short()
                      : m.stv_elected({ count: row.elected })}</span
                  >
                {:else if row.out != null}
                  <span class="ps-4 text-xs text-pretty text-muted-foreground"
                    >{m.stv_excluded({ count: row.out })}</span
                  >
                {:else if count.last > 1}
                  <span class="ps-4 text-xs text-pretty text-muted-foreground"
                    >{m.stv_unelected()}</span
                  >
                {/if}
              </span>
            </th>
            {#each rounds as round (round)}
              {const votes = $derived(row.votes[round - 1])}
              {const before = $derived(
                round === 1 ? null : row.votes[round - 2],
              )}
              {const change = $derived(
                before == null ? null : (votes ?? 0) - before,
              )}
              <td
                class={cn("px-2 py-1.5 align-top", {
                  "bg-secondary": round === count.round,
                })}
              >
                {#if votes != null}
                  <span class="grid justify-items-end gap-1">
                    <span class="grid justify-items-end">
                      <span
                        class={cn("font-figures tabular-nums", {
                          "font-semibold": votes >= stv.quota,
                        })}>{figure(votes)}</span
                      >
                      {#if change != null && Math.abs(change) >= 0.0005}
                        <span
                          class="font-figures text-xs text-muted-foreground tabular-nums"
                          >{signed(change)}</span
                        >
                      {/if}
                    </span>
                    <span
                      class="relative bg-secondary block-0.75 inline-full"
                      aria-hidden="true"
                    >
                      <span
                        class="absolute inset-y-0 inset-s-0 bg-(--party)"
                        style:inline-size="{Math.min(1, votes / stv.quota) *
                          100}%"
                      ></span>
                    </span>
                    {#if row.elected === round}
                      <span class="text-xs font-semibold"
                        >{m.outcome_elected()}</span
                      >
                    {/if}
                  </span>
                {:else if before != null}
                  <span
                    class="block text-end font-figures text-xs text-muted-foreground tabular-nums"
                    >{signed(-before)}</span
                  >
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
            {const prior = $derived(
              index === 0 ? null : count.exhausted[index - 1],
            )}
            <td
              class={cn("px-2 py-1.5 text-end align-top", {
                "bg-secondary": index + 1 === count.round,
              })}
            >
              {#if spent != null}
                <span class="grid justify-items-end">
                  <span class="font-figures tabular-nums">{figure(spent)}</span>
                  {#if prior != null && Math.abs(spent - prior) >= 0.0005}
                    <span class="font-figures text-xs tabular-nums"
                      >{signed(spent - prior)}</span
                    >
                  {/if}
                </span>
              {/if}
            </td>
          {/each}
        </tr>
      </tbody>
    </table>
  </div>
</section>
