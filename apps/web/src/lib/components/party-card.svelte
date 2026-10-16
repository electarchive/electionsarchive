<script lang="ts">
  import { resolve } from "$app/paths";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import { createQuery } from "@tanstack/svelte-query";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";

  const { id, color = null }: { id: string; color?: string | null } = $props();

  const KINDS: Record<string, () => string> = {
    party: m.party_kind_party,
    independent_group: m.party_kind_independent_group,
    list_vehicle: m.party_kind_list_vehicle,
    federation: m.party_kind_federation,
    bloc: m.party_kind_bloc,
    nuance: m.party_kind_nuance,
    parliamentary_group: m.party_kind_parliamentary_group,
  };
  const RELATIONS: Record<
    string,
    [(input: { name: string }) => string, (input: { name: string }) => string]
  > = {
    merged_into: [m.rel_merged_into_out, m.rel_merged_into_in],
    split_from: [m.rel_split_from_out, m.rel_split_from_in],
    successor_of: [m.rel_successor_of_out, m.rel_successor_of_in],
    refounded_as: [m.rel_refounded_as_out, m.rel_refounded_as_in],
    absorbed_by: [m.rel_absorbed_by_out, m.rel_absorbed_by_in],
    list_vehicle_for: [m.rel_list_vehicle_for_out, m.rel_list_vehicle_for_in],
  };
  const AFFILIATIONS: Record<string, (input: { name: string }) => string> = {
    ep_group: m.aff_ep_group,
    federation: m.aff_federation,
    international: m.aff_international,
    regional_branch: m.aff_regional_branch,
  };

  const query = createQuery(() => ({
    ...orpc.parties.get.queryOptions({ input: { id } }),
    meta: { quiet: true },
  }));

  const links = $derived(
    query.data == null
      ? []
      : [
          ...query.data.relations.flatMap((relation) => {
            const phrase =
              RELATIONS[relation.kind]?.[relation.outgoing ? 0 : 1];
            return phrase == null
              ? []
              : [
                  {
                    text: phrase({ name: relation.party.label }),
                    party: relation.party,
                    year: relation.date?.slice(0, 4) ?? null,
                  },
                ];
          }),
          ...query.data.parents.flatMap((parent) => {
            const phrase = AFFILIATIONS[parent.kind];
            return phrase == null
              ? []
              : [
                  {
                    text: phrase({ name: parent.party.label }),
                    party: parent.party,
                    year: null,
                  },
                ];
          }),
        ],
  );
</script>

{#if query.data == null}
  <p class="p-3 text-[0.8125rem] text-muted-foreground">
    {query.isError ? m.preview_failed() : m.preview_loading()}
  </p>
{:else}
  {const party = $derived(query.data)}
  <div class="grid gap-0.5 px-3 pbs-2.5 pbe-2">
    <p
      class="flex items-baseline gap-2 font-serif text-[1.0625rem]/tight font-[620]"
    >
      <span
        class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
        style:background-color={color ?? swatch(party.color, `party:${id}`)}
      ></span>
      <span>
        {party.name}
        {#if party.short != null && party.short !== party.name}
          <span
            class="ms-1 font-sans text-[0.8125rem] font-normal text-muted-foreground"
            >{party.short}</span
          >
        {/if}
      </span>
    </p>
    {#if party.polity != null}
      <p class="text-[0.8125rem] text-muted-foreground">
        {m.party_in({
          kind: KINDS[party.kind]?.() ?? party.kind,
          polity: party.polity.name,
        })}
      </p>
    {/if}
  </div>
  {#if party.span != null}
    <dl class="grid grid-cols-2 gap-px border-bs bg-border">
      <div class="bg-popover px-3 py-2">
        <dt class="text-xs text-muted-foreground">{m.party_ballot()}</dt>
        <dd class="font-mono text-[0.9375rem] tabular-nums">
          {party.span.from.slice(0, 4) === party.span.to.slice(0, 4)
            ? party.span.from.slice(0, 4)
            : `${party.span.from.slice(0, 4)}–${party.span.to.slice(0, 4)}`}
        </dd>
      </div>
      <div class="bg-popover px-3 py-2">
        <dt class="text-xs text-muted-foreground">{m.party_contests()}</dt>
        <dd class="font-mono text-[0.9375rem] tabular-nums">
          {count(party.span.contests)}
        </dd>
      </div>
    </dl>
  {/if}
  {#if links.length > 0}
    <ul class="grid gap-1 border-bs px-3 py-2 text-[0.8125rem]/snug">
      {#each links as link, index (index)}
        <li class="flex items-baseline gap-1.5">
          <span
            class="shrink-0 self-center rounded-xs block-2 inline-2"
            style:background-color={swatch(
              link.party.color,
              `party:${link.party.id}`,
            )}
          ></span>
          <span class="flex-1">{link.text}</span>
          {#if link.year != null}
            <span class="font-mono text-xs text-muted-foreground tabular-nums"
              >{link.year}</span
            >
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
  <div
    class="flex flex-wrap items-center gap-x-4 gap-y-1 border-bs px-3 py-2 text-[0.8125rem]"
  >
    <a
      class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
      href={resolve("/id/[id]", { id })}>{m.card_open()}</a
    >
    {#if party.wikidata != null}
      <a
        class="inline-flex items-center gap-1 underline decoration-border underline-offset-4 hover:decoration-foreground"
        href="https://www.wikidata.org/wiki/{party.wikidata}"
        rel="external noopener"
        >{m.card_wikidata()}<ArrowUpRightIcon class="block-3 inline-3" /></a
      >
    {/if}
  </div>
{/if}
