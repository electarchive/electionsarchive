<script lang="ts">
  import { resolve } from "$app/paths";
  import Name from "#lib/components/name.svelte";
  import Term from "#lib/components/term.svelte";
  import { count as figure } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    additional,
  }: {
    additional: NonNullable<
      Extract<PageData, { kind: "contest" }>["additional"]
    >;
  } = $props();

  const uid = $props.id();
  const list = new Intl.ListFormat(getLocale(), {
    style: "long",
    type: "conjunction",
  });
  const seats = $derived(
    Array.from({ length: additional.picks.length }, (_, seat) => seat),
  );
  const parties = $derived(
    additional.parties.filter(
      (party) =>
        party.constituencies + party.declared + (party.computed ?? 0) > 0 ||
        party.key === additional.next?.key,
    ),
  );
  const labels = $derived(
    new Map(additional.parties.map((party) => [party.key, party])),
  );
  const status = $derived.by(() => {
    if (!additional.isCounted) {
      return m.ams_uncounted();
    }
    if (additional.isReproduced) {
      return m.ams_reproduced();
    }
    return m.ams_differs({
      parties: list.format(
        additional.parties
          .filter((party) => party.computed !== party.declared)
          .map((party) =>
            m.ams_differs_party({
              party: party.short ?? party.label,
              computed: figure(party.computed ?? 0),
              declared: figure(party.declared),
            }),
          ),
      ),
    });
  });
  const next = $derived(
    additional.next == null ? null : labels.get(additional.next.key),
  );
  const members = $derived(
    Map.groupBy(additional.members, (member) => member.key)
      .entries()
      .toArray()
      .toSorted(
        ([a], [b]) =>
          parties.findIndex((party) => party.key === a) -
          parties.findIndex((party) => party.key === b),
      ),
  );
</script>

<section
  class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0"
  aria-labelledby="{uid}-title"
>
  <div class="grid gap-1">
    <h2 id="{uid}-title" class="text-lg font-bold">{m.ams_title()}</h2>
    <p
      class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[72ch]"
    >
      {m.ams_lede({ seats: figure(additional.seats) })}
      {status}
    </p>
  </div>
  <div class="overflow-x-auto">
    <table class="border-collapse text-sm inline-full">
      <thead>
        <tr class="border-be text-[0.8125rem] text-muted-foreground">
          <th
            class="sticky inset-s-0 z-10 bg-card pe-3 pbe-1.5 text-start align-bottom font-normal"
            scope="col"
          >
            {m.col_party()}
          </th>
          {#if additional.isCounted}
            <th
              class="px-2 pbe-1.5 text-end align-bottom font-normal"
              scope="col"
            >
              {m.col_list_votes()}
            </th>
          {/if}
          <th
            class="px-2 pbe-1.5 text-end align-bottom font-normal"
            scope="col"
          >
            {m.col_constituency_seats()}
          </th>
          {#each seats as seat (seat)}
            <th
              class="px-2 pbe-1.5 text-end align-bottom font-normal min-inline-20"
              scope="col"
            >
              <Term
                label={m.ams_seat({ count: seat + 1 })}
                definition={m.term_quotient()}
              />
            </th>
          {/each}
          <th
            class="px-2 pbe-1.5 text-end align-bottom font-normal"
            scope="col"
          >
            {m.col_list_seats()}
          </th>
          <th
            class="ps-2 pbe-1.5 text-end align-bottom font-normal"
            scope="col"
          >
            {m.col_total_seats()}
          </th>
        </tr>
      </thead>
      <tbody>
        {#each parties as party (party.key)}
          {const color = $derived(swatch(party.color, party.key))}
          <tr class="border-be" style:--party={color}>
            <th
              class="sticky inset-s-0 z-10 bg-card py-1.5 pe-3 text-start font-normal max-inline-40 min-inline-32 sm:max-inline-64 sm:min-inline-48"
              scope="row"
            >
              <Name
                class="truncate max-inline-56"
                label={party.label}
                {color}
                party={party.isParty ? party.key : null}
              />
            </th>
            {#if additional.isCounted}
              <td class="px-2 py-1.5 text-end font-figures tabular-nums">
                {party.votes == null ? "" : figure(party.votes)}
              </td>
            {/if}
            <td class="px-2 py-1.5 text-end font-figures tabular-nums">
              {figure(party.constituencies)}
            </td>
            {#each seats as seat (seat)}
              {const quotient = $derived(party.quotients[seat])}
              {const isPick = $derived(additional.picks[seat] === party.key)}
              <td
                class={cn("px-2 py-1.5 text-end font-figures tabular-nums", {
                  "font-semibold text-foreground": isPick,
                  "text-muted-foreground": !isPick,
                })}
                style:background={isPick
                  ? "color-mix(in oklab, var(--party) 22%, var(--card))"
                  : undefined}
              >
                {quotient == null ? "" : figure(Math.round(quotient))}
              </td>
            {/each}
            <td class="px-2 py-1.5 text-end font-figures tabular-nums">
              {figure(party.declared)}
            </td>
            <td
              class="py-1.5 ps-2 text-end font-figures font-semibold tabular-nums"
            >
              {figure(party.constituencies + party.declared)}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#if next != null && additional.next != null}
    <p class="text-[0.8125rem] text-pretty text-muted-foreground">
      {m.ams_next({
        party: next.label,
        quotient: figure(Math.round(additional.next.quotient)),
      })}
    </p>
  {/if}
  <div class="grid gap-x-6 gap-y-3 sm:grid-cols-2">
    <div class="grid content-start gap-1.5">
      <h3 class="text-sm font-semibold">{m.ams_constituency_members()}</h3>
      <ul class="grid gap-1 text-sm">
        {#each additional.constituencies as seat (seat.name)}
          {const party = $derived(labels.get(seat.key))}
          <li class="flex flex-wrap gap-x-1.5">
            {#if seat.path == null}
              <span>{seat.name}</span>
            {:else}
              <a
                class="underline decoration-border underline-offset-2 hover:decoration-foreground"
                href={resolve("/[...path]", { path: seat.path })}>{seat.name}</a
              >
            {/if}
            <span class="text-muted-foreground">
              {#if seat.member != null}
                <Name
                  label={seat.member}
                  color={swatch(party?.color ?? null, seat.key)}
                  party={party?.isParty ? seat.key : null}
                />
              {/if}
            </span>
          </li>
        {/each}
      </ul>
    </div>
    {#if members.length > 0}
      <div class="grid content-start gap-1.5">
        <h3 class="text-sm font-semibold">{m.ams_list_members()}</h3>
        <ul class="grid gap-1 text-sm">
          {#each members as [key, elected] (key)}
            {const party = $derived(labels.get(key))}
            <li class="grid gap-0.5">
              <Name
                class="font-medium"
                label={party?.label ?? ""}
                color={swatch(party?.color ?? null, key)}
                party={party?.isParty ? key : null}
              />
              <span class="ps-4 text-pretty">
                {#each elected as member, index (member.name)}
                  {#if index > 0},
                  {/if}
                  {#if member.path == null}
                    {member.name}
                  {:else}
                    <a
                      class="underline decoration-border underline-offset-2 hover:decoration-foreground"
                      href={resolve("/[...path]", { path: member.path })}
                      >{member.name}</a
                    >
                  {/if}
                {/each}
              </span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  </div>
</section>
