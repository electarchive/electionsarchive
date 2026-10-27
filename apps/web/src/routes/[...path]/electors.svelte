<script lang="ts">
  import { resolve } from "$app/paths";
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import Name from "#lib/components/name.svelte";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, date, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import Balance from "./balance.svelte";
  import Preview from "./preview.svelte";

  let {
    electors,
    ladder = null,
    focus = $bindable(null),
  }: {
    electors: {
      rows: Array<{
        key: string;
        label: string;
        short: string | null;
        color: string | null;
        person: string | null;
        party: string | null;
        lean: number | null;
        votes: number;
      }>;
      appointed: number | null;
      link: { path: string | null; date: string | null; popular: boolean };
    };
    ladder?: Array<{
      k: number;
      name: string;
      path: string | null;
      seats: number;
      color: string;
      leader: string;
      margin: number;
    }> | null;
    focus?: number | null;
  } = $props();

  const total = $derived.by(() => {
    let sum = 0;
    for (const row of electors.rows) {
      sum += row.votes;
    }
    return sum;
  });
  const parts = $derived(
    electors.rows
      .toSorted(
        (a, b) => (a.lean ?? 1e9) - (b.lean ?? 1e9) || b.votes - a.votes,
      )
      .map((row) => ({
        key: row.key,
        label: row.label,
        color: swatch(row.color, row.key),
        value: row.votes,
        person: row.person,
        party: row.party,
        detail:
          row.short == null || row.label.includes(row.short) ? null : row.short,
      })),
  );
  const others = $derived(
    electors.rows.toSorted((a, b) => b.votes - a.votes).slice(2),
  );
  const rungs = TooltipPrimitive.createTether<number>();
  const uid = $props.id();
</script>

{#snippet rungsBar()}
  {#each ladder ?? [] as rung, index (rung.k)}
    <Tooltip.Trigger
      tether={rungs}
      payload={index}
      tabindex={-1}
      onpointerenter={() => (focus = rung.k)}
      onpointerleave={() => (focus = null)}
    >
      {#snippet child({ props })}
        <svelte:element
          this={rung.path == null ? "span" : "a"}
          {...props}
          type={undefined}
          href={rung.path == null
            ? undefined
            : resolve("/[...path]", { path: rung.path })}
          class={cn("block shrink-0 basis-0 transition-opacity", {
            "opacity-45": focus != null && focus !== rung.k,
          })}
          style:background-color={rung.color}
          style:flex-grow={rung.seats}
        />
      {/snippet}
    </Tooltip.Trigger>
  {/each}
{/snippet}

<section
  class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5"
  aria-labelledby="{uid}-title"
>
  <Balance
    {parts}
    total={electors.appointed ?? total}
    isCount
    isLead
    isWhole
    isLabeled
    segments={ladder == null ? undefined : rungsBar}
  >
    {#snippet middle()}
      <h2
        id="{uid}-title"
        class="pbe-0.5 text-[0.8125rem] text-muted-foreground max-sm:sr-only"
      >
        {m.electors_title()}
      </h2>
    {/snippet}
  </Balance>
  <Tooltip.Root tether={rungs} delayDuration={0}>
    {#snippet children({ payload })}
      {const rung = $derived(
        payload == null ? undefined : ladder?.at(Number(payload)),
      )}
      {#if rung != null}
        <Tooltip.Content collisionPadding={8} class="grid gap-0.5 leading-snug">
          <span class="font-semibold">{rung.name}</span>
          <span class="font-figures text-xs tabular-nums"
            >{m.record_electors({ count: count(rung.seats) })}</span
          >
          <span class="text-xs opacity-80"
            >{rung.leader}
            <span class="font-figures tabular-nums">{points(rung.margin)}</span
            ></span
          >
        </Tooltip.Content>
      {/if}
    {/snippet}
  </Tooltip.Root>
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
            <span class="font-figures tabular-nums">{count(row.votes)}</span>
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
