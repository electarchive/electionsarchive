<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import { SvelteSet } from "svelte/reactivity";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";

  const { id, place }: { id: string; place: string } = $props();

  const uid = $props.id();

  const record = createQuery(() =>
    orpc.polities.record.queryOptions({ input: { id } }),
  );

  const opened = new SvelteSet<string>();
  let isAll = $state(false);

  const parties = $derived(
    new Map((record.data?.parties ?? []).map((party) => [party.id, party])),
  );
  const series = $derived(
    (record.data?.series ?? []).filter(
      (entry) => !entry.isAbroad && entry.points.length > 0,
    ),
  );
</script>

{#if series.length > 0}
  <section
    class="col-span-12 grid content-start gap-4 bg-card px-3.5 pbs-3.5 pbe-4"
    aria-labelledby="{uid}-title"
  >
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="heading">
        {m.scrubber_title({ place })}
      </h2>
      <p
        class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
      >
        {m.offices_lede({ place })}
      </p>
    </div>
    <div
      class="grid grid-cols-[repeat(auto-fit,minmax(min(100%,19rem),1fr))] gap-x-6 gap-y-5"
    >
      {#each isAll ? series : series.slice(0, 9) as entry (entry.id)}
        {const isPartyless = $derived(
          entry.points.every((point) =>
            point.parties.every(
              (row) => row.party == null && row.isIndependent !== true,
            ),
          ),
        )}
        {const points = $derived(entry.points.toReversed())}
        {const isOpen = $derived(opened.has(entry.id))}
        <div class="grid content-start gap-1.5">
          <h3 class="text-sm font-semibold">{entry.name}</h3>
          <ol class="grid text-sm">
            {#each isOpen || points.length <= 6 ? points : points.slice(0, 5) as point (point.date)}
              {const top = $derived(
                point.parties.toSorted((a, b) => b.seats - a.seats),
              )}
              {const path = $derived(point.contest ?? point.path)}
              <li
                class="grid grid-cols-[3rem_minmax(0,1fr)_auto] items-baseline gap-x-3 border-be border-border/60 py-1"
              >
                {#if path == null}
                  <span class="font-figures tabular-nums">{point.year}</span>
                {:else}
                  <a
                    class="font-figures tabular-nums underline decoration-border underline-offset-4 hover:decoration-foreground"
                    href={resolve("/[...path]", { path })}>{point.year}</a
                  >
                {/if}
                {#if entry.isOffice && point.winner != null}
                  <span class="flex items-baseline gap-1.5 min-inline-0">
                    <span
                      class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
                      style:background-color={swatch(
                        parties.get(top[0]?.party ?? "")?.color ?? null,
                        top[0]?.party == null ? "" : `party:${top[0].party}`,
                      )}
                    ></span>
                    <span class="truncate font-semibold">{point.winner}</span>
                    {#if point.isRunoff}
                      <span class="shrink-0 text-xs text-muted-foreground"
                        >{m.offices_runoff()}</span
                      >
                    {/if}
                  </span>
                  <span
                    class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                    >{point.share == null
                      ? ""
                      : point.share >= 1
                        ? m.map_unopposed()
                        : percent(point.share)}</span
                  >
                {:else if top.every((row) => row.party == null && row.isIndependent !== true)}
                  <span
                    class="col-span-2 text-[0.8125rem] text-muted-foreground"
                    >{isPartyless
                      ? m.record_seats_filled({ count: point.filled })
                      : m.record_filled({ count: point.filled })}</span
                  >
                {:else}
                  <span
                    class="col-span-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[0.8125rem]"
                  >
                    {#each top.slice(0, 4) as row (row.party ?? "none")}
                      {const party = $derived(parties.get(row.party ?? ""))}
                      <span class="flex items-baseline gap-1.5">
                        <span
                          class="self-center rounded-xs block-2.5 inline-2.5"
                          style:background-color={swatch(
                            party?.color ?? null,
                            row.party == null ? "" : `party:${row.party}`,
                          )}
                        ></span>
                        <span
                          >{row.label ??
                            party?.short ??
                            party?.label ??
                            (row.isIndependent === true
                              ? m.seats_independents()
                              : m.record_none())}</span
                        >
                        <span class="font-figures font-semibold tabular-nums"
                          >{count(row.seats)}</span
                        >
                      </span>
                    {/each}
                  </span>
                {/if}
              </li>
            {/each}
          </ol>
          {#if points.length > 6}
            <button
              type="button"
              class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
              onclick={() => {
                if (isOpen) {
                  opened.delete(entry.id);
                } else {
                  opened.add(entry.id);
                }
              }}
              >{isOpen
                ? m.regions_fewer()
                : m.regions_more({ count: count(points.length) })}</button
            >
          {/if}
        </div>
      {/each}
    </div>
    {#if series.length > 9}
      <button
        type="button"
        class="justify-self-start rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary pointer-coarse:block-10"
        aria-expanded={isAll}
        onclick={() => (isAll = !isAll)}
        >{isAll
          ? m.regions_fewer()
          : m.regions_more({ count: count(series.length) })}</button
      >
    {/if}
  </section>
{/if}
