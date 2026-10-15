<script lang="ts">
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";

  const {
    parties,
  }: {
    parties: Array<{
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      seats: number;
    }>;
  } = $props();

  const total = $derived.by(() => {
    let sum = 0;
    for (const party of parties) {
      sum += party.seats;
    }
    return sum;
  });
  const majority = $derived(Math.floor(total / 2) + 1);
  const first = $derived(parties[0]);
  const second = $derived(parties[1]);
  const others = $derived(parties.slice(2));
  const order = $derived([
    ...(first == null ? [] : [first]),
    ...others,
    ...(second == null ? [] : [second]),
  ]);
</script>

{#snippet side(party: (typeof parties)[number] | undefined)}
  {#if party != null}
    <div
      class="grid gap-0.5 min-inline-0 last:text-end"
      style:--party={swatch(party.color, party.key)}
    >
      <span
        class="font-serif text-4xl/none font-[640] tracking-[-0.01em] tabular-nums max-sm:text-3xl/none"
        >{count(party.seats)}</span
      >
      <span class="truncate text-sm">
        <span
          class="me-1 inline-block rounded-xs bg-(--party) align-[-1px] block-2.5 inline-2.5"
        ></span>
        <span class="font-semibold">{party.label}</span>
        {#if party.short != null && party.short !== party.label}
          <span class="text-muted-foreground max-sm:hidden">{party.short}</span>
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
      aria-label={parties
        .map((party) => `${party.label} ${count(party.seats)}`)
        .join(", ")}
    >
      {#each order as party (party.key)}
        <span
          class="block shrink-0"
          style:background-color={swatch(party.color, party.key)}
          style:width="{(party.seats / total) * 100}%"
          title="{party.label} {count(party.seats)}"
        ></span>
      {/each}
    </div>
    <span
      class="absolute inset-bs-0 -translate-x-1/2 bg-foreground block-[calc(100%-1rem)] inline-0.5"
      style:inset-inline-start="{(majority / total) * 100}%"
      aria-hidden="true"
    ></span>
    <span
      class="absolute inset-be-0 -translate-x-1/2 text-[0.8125rem] whitespace-nowrap text-muted-foreground"
      style:inset-inline-start="{(majority / total) * 100}%"
      >{m.electors_majority({ count: count(majority) })}</span
    >
  </div>
  {#if others.length > 0}
    <ul class="flex flex-wrap justify-center gap-x-4 gap-y-1 text-[0.8125rem]">
      {#each others as party (party.key)}
        <li class="flex items-center gap-1.5">
          <span
            class="rounded-xs block-2.5 inline-2.5"
            style:background-color={swatch(party.color, party.key)}
          ></span>
          <span class="font-semibold">{party.label}</span>
          <span class="font-mono tabular-nums">{count(party.seats)}</span>
        </li>
      {/each}
    </ul>
  {/if}
</section>
