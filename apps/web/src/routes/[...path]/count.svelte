<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    count,
    here,
    isDerived,
  }: {
    count: NonNullable<
      Extract<PageData, { kind: "contest" }>["contest"]["count"]
    >;
    here: string;
    isDerived: boolean;
  } = $props();

  const uid = $props.id();
  const index = $derived(count.round - 1);
  const standing = $derived.by(() => {
    const rows = count.rows.filter((row) => row.votes[index] != null);
    let total = 0;
    for (const row of rows) {
      total += row.votes[index] ?? 0;
    }
    return rows.map((row) => ({
      ...row,
      share: total === 0 ? 0 : (row.votes[index] ?? 0) / total,
    }));
  });
  const leaving = $derived(
    count.round === count.last
      ? []
      : standing.filter((row) => row.votes[index + 1] == null),
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
  const rounds = $derived(
    Array.from({ length: count.last }, (_, step) => step + 1),
  );
  const list = new Intl.ListFormat(getLocale(), {
    style: "long",
    type: "conjunction",
  });
  const action = $derived.by(() => {
    const step = count.stv?.steps[index];
    if (step == null) {
      return null;
    }
    const label = (keys: Array<string>) =>
      list.format(
        keys.map(
          (key) => count.rows.find((row) => row.key === key)?.label ?? "",
        ),
      );
    return {
      text:
        step.kind === "first"
          ? m.stv_first()
          : step.kind === "surplus"
            ? m.stv_surplus({ names: label(step.from) })
            : step.kind === "exclusion"
              ? m.stv_exclusion({ names: label(step.from) })
              : m.stv_none(),
      elected: step.elected.length === 0 ? null : label(step.elected),
    };
  });
</script>

{#snippet step(round: number, label: string, Icon: typeof ChevronLeftIcon)}
  {#if round < 1 || round > count.last}
    <span
      class="flex shrink-0 items-center justify-center rounded-sm border border-input bg-card text-muted-foreground opacity-40 block-8 inline-8"
      aria-hidden="true"><Icon class="block-4 inline-4" /></span
    >
  {:else}
    <a
      class="flex shrink-0 items-center justify-center rounded-sm border border-input bg-card keycap block-8 inline-8 hover:bg-secondary"
      href={href(round)}
      aria-label={label}
      data-sveltekit-reset="false"><Icon class="block-4 inline-4" /></a
    >
  {/if}
{/snippet}

<nav
  class="col-span-12 grid gap-2.5 bg-card px-3.5 pbs-3 pbe-3.5"
  aria-labelledby="{uid}-title"
>
  <div class="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="text-sm font-semibold">
        {m.count_title()}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground">
        {#if action == null}
          {count.round === count.last
            ? m.count_final()
            : m.count_of({ round: count.round, last: count.last })}.
          {m.count_continuing({ count: standing.length })}
        {:else}
          {m.stv_step({
            count: count.round,
            last: count.last,
            action: action.text,
          })}
          {#if action.elected != null}
            {m.stv_step_elected({ names: action.elected })}
          {/if}
        {/if}
      </p>
    </div>
    <div class="flex items-stretch gap-1.5">
      {@render step(count.round - 1, m.count_previous(), ChevronLeftIcon)}
      <ol class="flex">
        {#each rounds as round (round)}
          <li class="-ms-px first:ms-0">
            <a
              class={cn(
                "relative flex items-center justify-center border border-input bg-card px-2 font-figures text-[0.8125rem] tabular-nums block-8 min-inline-8",
                {
                  "z-10 border-foreground highlighter font-semibold":
                    round === count.round,
                  "text-muted-foreground hover:z-10 hover:border-foreground hover:text-foreground":
                    round !== count.round,
                },
              )}
              href={href(round)}
              aria-current={round === count.round ? "step" : undefined}
              aria-label={count.stv != null
                ? m.stv_count({ count: round })
                : round === count.last
                  ? m.count_final()
                  : m.count_round({ round })}
              data-sveltekit-reset="false">{round}</a
            >
          </li>
        {/each}
      </ol>
      {@render step(count.round + 1, m.count_next(), ChevronRightIcon)}
    </div>
  </div>
  <div
    class="flex gap-px overflow-hidden rounded-xs block-2"
    aria-hidden="true"
  >
    {#each standing as row (row.key)}
      <span
        class="bg-(--party)"
        style:--party={swatch(row.color, row.key)}
        style:flex-grow={row.share}
      ></span>
    {/each}
  </div>
  {#if leaving.length > 0 && action == null}
    <p class="text-[0.8125rem]">
      {m.count_eliminated({
        names: new Intl.ListFormat(getLocale(), {
          style: "long",
          type: "conjunction",
        }).format(leaving.map((row) => row.label)),
      })}
    </p>
  {/if}
  {#if isDerived}
    <p class="text-[0.8125rem] text-muted-foreground">
      {m.count_derived()}
      <a
        class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        href="#notes">{m.count_derived_link()}</a
      >
    </p>
  {/if}
  <p class="sr-only">
    {#each standing as row (row.key)}{row.label} {percent(row.share)}.
    {/each}
  </p>
</nav>
