<script lang="ts">
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import Name from "#lib/components/name.svelte";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import Preview from "./preview.svelte";

  const {
    electors,
  }: {
    electors: {
      rows: Array<{
        key: string;
        label: string;
        short: string | null;
        color: string | null;
        person: string | null;
        party: string | null;
        votes: number;
      }>;
      appointed: number | null;
      link: { path: string | null; date: string | null; popular: boolean };
    };
  } = $props();

  const total = $derived.by(() => {
    let sum = 0;
    for (const row of electors.rows) {
      sum += row.votes;
    }
    return sum;
  });
  const majority = $derived(Math.floor((electors.appointed ?? total) / 2) + 1);
  const first = $derived(electors.rows[0]);
  const second = $derived(electors.rows[1]);
  const others = $derived(electors.rows.slice(2));
  const order = $derived([
    ...(first == null ? [] : [first]),
    ...others,
    ...(second == null ? [] : [second]),
  ]);
  const tether = TooltipPrimitive.createTether<number>();
</script>

{#snippet about(row: (typeof electors.rows)[number] | undefined)}
  {#if row != null}
    <Tooltip.Content
      collisionPadding={8}
      class="grid gap-0.5 leading-snug inline-[min(16rem,calc(100vw-2rem))]"
    >
      <span class="flex items-baseline gap-1.5">
        <span
          class="shrink-0 self-center rounded-xs block-2 inline-2"
          style:background-color={swatch(row.color, row.key)}
        ></span>
        <span class="font-semibold">{row.label}</span>
      </span>
      {#if row.short != null && row.short !== row.label}
        <span class="text-xs opacity-80">{row.short}</span>
      {/if}
      <span
        class="flex items-baseline justify-between gap-3 font-mono text-xs tabular-nums"
        >{m.series_electoral({
          votes: count(row.votes),
          total: count(electors.appointed ?? total),
        })}<span class="opacity-70"
          >{percent(row.votes / (electors.appointed ?? total))}</span
        ></span
      >
    </Tooltip.Content>
  {/if}
{/snippet}

{#snippet side(row: (typeof electors.rows)[number] | undefined)}
  {#if row != null}
    <div
      class="grid gap-0.5 min-inline-0 last:text-end"
      style:--party={swatch(row.color, row.key)}
    >
      <span
        class="font-serif text-4xl/none font-[640] tracking-[-0.01em] tabular-nums max-sm:text-3xl/none"
        >{count(row.votes)}</span
      >
      <span class="truncate text-sm">
        <Name
          class="font-semibold"
          label={row.label}
          color={swatch(row.color, row.key)}
          person={row.person}
          party={row.party}
        />
        {#if row.short != null && row.short !== row.label}
          <Name
            class="text-muted-foreground max-sm:hidden"
            label={row.short}
            party={row.party}
          />
        {/if}
      </span>
    </div>
  {/if}
{/snippet}

<section
  class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5"
  aria-labelledby="electors-title"
>
  <div class="flex items-end justify-between gap-4">
    {@render side(first)}
    <h2
      id="electors-title"
      class="pbe-0.5 text-[0.8125rem] text-muted-foreground max-sm:sr-only"
    >
      {m.electors_title()}
    </h2>
    {@render side(second)}
  </div>
  <div class="relative pbs-1.5 pbe-5">
    <div
      class="flex gap-px overflow-hidden rounded-xs bg-card block-6"
      role="img"
      aria-label={electors.rows
        .map((row) => `${row.label} ${count(row.votes)}`)
        .join(", ")}
    >
      {#each order as row, index (row.key)}
        <Tooltip.Trigger {tether} payload={index} tabindex={-1}>
          {#snippet child({ props })}
            <span
              {...props}
              class="block shrink-0"
              style:background-color={swatch(row.color, row.key)}
              style:width="{(row.votes / total) * 100}%"
            ></span>
          {/snippet}
        </Tooltip.Trigger>
      {/each}
    </div>
    <Tooltip.Root {tether} delayDuration={150}>
      {#snippet children({ payload })}
        {@render about(payload == null ? undefined : order.at(Number(payload)))}
      {/snippet}
    </Tooltip.Root>
    <span
      class="absolute inset-bs-0 -translate-x-1/2 bg-foreground block-[calc(100%-1.125rem)] inline-0.5"
      style:inset-inline-start="{Math.min(1, majority / total) * 100}%"
      aria-hidden="true"
    ></span>
    <span
      class="absolute inset-be-0 -translate-x-1/2 text-[0.8125rem] whitespace-nowrap text-muted-foreground"
      style:inset-inline-start="{Math.min(1, majority / total) * 100}%"
      >{m.electors_majority({ count: count(majority) })}</span
    >
  </div>
  {#if others.length > 0 || electors.link.path != null}
    <div
      class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1.5 text-[0.8125rem]"
    >
      <ul class="flex flex-wrap gap-x-4 gap-y-1">
        {#each others as row (row.key)}
          <li class="flex items-baseline gap-1.5">
            <Name
              class="font-semibold"
              label={row.label}
              color={swatch(row.color, row.key)}
              person={row.person}
              party={row.party}
            />
            <span class="font-mono tabular-nums">{count(row.votes)}</span>
          </li>
        {/each}
      </ul>
      {#if electors.link.path != null}
        <Preview
          class="text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-foreground"
          path={electors.link.path}
          >{electors.link.popular
            ? m.electors_popular({ date: date(electors.link.date) })
            : m.electors_cast({ date: date(electors.link.date) })}</Preview
        >
      {/if}
    </div>
  {/if}
</section>
