<script lang="ts">
  import { resolve } from "$app/paths";
  import { SvelteMap } from "svelte/reactivity";
  import Name from "#lib/components/name.svelte";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";

  const {
    councils,
  }: {
    councils: Extract<PageData, { kind: "election" }>["election"]["councils"];
  } = $props();

  const uid = $props.id();

  let isAll = $state(false);

  const tally = $derived.by(() => {
    const parties = new SvelteMap<
      string,
      {
        key: string;
        label: string;
        color: string;
        seats: number;
        controlled: number;
      }
    >();
    let seats = 0;
    for (const council of councils) {
      for (const party of council.parties) {
        const entry = parties.get(party.key) ?? {
          key: party.key,
          label: party.short ?? party.label,
          color: swatch(party.color, party.key),
          seats: 0,
          controlled: 0,
        };
        entry.seats += party.seats;
        if (council.control?.key === party.key) {
          entry.controlled += 1;
        }
        parties.set(party.key, entry);
        seats += party.seats;
      }
    }
    return {
      seats,
      parties: parties
        .values()
        .toArray()
        .toSorted((a, b) => b.seats - a.seats),
      none: councils.filter(
        (council) => council.control != null && council.control.key == null,
      ).length,
    };
  });
</script>

<section
  class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0"
  aria-labelledby="{uid}-title"
>
  <div class="grid gap-1">
    <h2 id="{uid}-title" class="text-lg font-bold">{m.councils_title()}</h2>
    <p
      class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[72ch]"
    >
      {m.councils_lede({ count: count(councils.length) })}
    </p>
  </div>
  <div class="grid gap-2">
    <div
      class="flex overflow-hidden rounded-xs bg-muted block-3"
      aria-hidden="true"
    >
      {#each tally.parties as party (party.key)}
        <span
          class="block-full"
          style:inline-size="{(party.seats / Math.max(1, tally.seats)) * 100}%"
          style:background-color={party.color}
        ></span>
      {/each}
    </div>
    <div class="overflow-x-auto overflow-y-hidden overscroll-x-contain">
      <table class="border-collapse text-sm inline-full">
        <thead>
          <tr class="border-be text-[0.8125rem] text-muted-foreground">
            <th class="pe-3 pbe-1.5 text-start font-normal" scope="col">
              {m.col_party()}
            </th>
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col">
              {m.col_councillors()}
            </th>
            <th class="ps-2 pbe-1.5 text-end font-normal" scope="col">
              {m.col_councils_controlled()}
            </th>
          </tr>
        </thead>
        <tbody>
          {#each tally.parties.slice(0, 8) as party (party.key)}
            <tr class="border-be border-border/60">
              <th class="py-1.5 pe-3 text-start font-normal" scope="row">
                <span class="inline-flex items-center gap-1.5">
                  <span
                    class="shrink-0 rounded-xs block-2 inline-2"
                    style:background={party.color}
                  ></span>
                  <Name
                    label={party.label}
                    party={party.key}
                    color={party.color}
                    hasSwatch={false}
                  />
                </span>
              </th>
              <td class="px-2 py-1.5 text-end font-figures tabular-nums">
                {count(party.seats)}
              </td>
              <td class="py-1.5 ps-2 text-end font-figures tabular-nums">
                {party.controlled === 0 ? "" : count(party.controlled)}
              </td>
            </tr>
          {/each}
          {#if tally.none > 0}
            <tr class="border-be border-border/60">
              <th class="py-1.5 pe-3 text-start font-normal" scope="row">
                {m.control_none_short()}
              </th>
              <td class="px-2 py-1.5"></td>
              <td class="py-1.5 ps-2 text-end font-figures tabular-nums">
                {count(tally.none)}
              </td>
            </tr>
          {/if}
        </tbody>
      </table>
    </div>
  </div>
  <div class="overflow-x-auto overflow-y-hidden overscroll-x-contain">
    <table class="border-collapse text-sm inline-full">
      <thead>
        <tr class="border-be text-[0.8125rem] text-muted-foreground">
          <th class="pe-3 pbe-1.5 text-start font-normal" scope="col">
            {m.col_council()}
          </th>
          <th class="px-2 pbe-1.5 text-end font-normal" scope="col">
            {m.col_seats_up()}
          </th>
          <th
            class="px-2 pbe-1.5 text-start font-normal min-inline-40"
            scope="col"
          >
            {m.col_seats_by_party()}
          </th>
          <th class="ps-2 pbe-1.5 text-start font-normal" scope="col">
            {m.col_control()}
          </th>
        </tr>
      </thead>
      <tbody>
        {#each isAll ? councils : councils.slice(0, 20) as council, index (index)}
          <tr class="border-be border-border/60">
            <th class="py-1.5 pe-3 text-start font-normal" scope="row">
              {#if council.path == null}
                {council.name}
              {:else}
                <a
                  class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                  href={resolve("/[...path]", { path: council.path })}
                  >{council.name}</a
                >
              {/if}
            </th>
            <td class="px-2 py-1.5 text-end font-figures tabular-nums">
              {council.size != null && council.size > council.up
                ? m.polity_seats({
                    seats: count(council.up),
                    size: count(council.size),
                  })
                : count(council.up)}
            </td>
            <td class="px-2 py-1.5">
              <span class="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[0.8125rem]">
                {#each council.parties.slice(0, 4) as party (party.key)}
                  <span class="inline-flex items-center gap-1">
                    <span
                      class="shrink-0 rounded-xs block-2 inline-2"
                      style:background={swatch(party.color, party.key)}
                    ></span>
                    <Name
                      label={party.short ?? party.label}
                      party={party.key}
                      color={swatch(party.color, party.key)}
                      hasSwatch={false}
                    />
                    <span class="font-figures font-semibold tabular-nums"
                      >{count(party.seats)}</span
                    >
                  </span>
                {/each}
              </span>
            </td>
            <td class="py-1.5 ps-2 text-[0.8125rem]">
              {#if council.control != null}
                {#if council.control.key == null}
                  {m.control_none_short()}
                {:else}
                  <span class="inline-flex items-center gap-1">
                    <span
                      class="shrink-0 rounded-xs block-2 inline-2"
                      style:background={swatch(
                        council.control.color,
                        council.control.key,
                      )}
                    ></span>
                    {council.parties.find(
                      (party) => party.key === council.control?.key,
                    )?.short ?? council.control.label}
                  </span>
                {/if}
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#if councils.length > 20}
    <button
      type="button"
      class="justify-self-start rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary"
      aria-expanded={isAll}
      onclick={() => (isAll = !isAll)}
      >{isAll
        ? m.polity_results_fewer()
        : m.places_show_all({ count: String(councils.length) })}</button
    >
  {/if}
</section>
