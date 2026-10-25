<script lang="ts">
  import { resolve } from "$app/paths";
  import { SvelteSet } from "svelte/reactivity";
  import Name from "#lib/components/name.svelte";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Preview from "./preview.svelte";

  const {
    local,
    isOwn,
  }: {
    local: NonNullable<Extract<PageData, { kind: "day" }>["local"]>;
    isOwn: boolean;
  } = $props();

  const uid = $props.id();

  const unfolded = new SvelteSet<string>();

  const tiers = $derived(
    local.tiers.filter((tier) => !isOwn || tier.kind !== "county"),
  );
  const TIERS = {
    county: m.local_tier_county,
    municipal: m.local_tier_municipal,
    school: m.local_tier_school,
    special: m.local_tier_special,
  };
  const round = (runoff: { date: string; isGeneral: boolean }) => {
    const day = new Intl.DateTimeFormat(getLocale(), {
      month: "long",
      day: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${runoff.date}T00:00:00Z`));
    return runoff.isGeneral
      ? m.wards_round_general({ date: day })
      : m.wards_round_runoff({ date: day });
  };
</script>

{#if tiers.length > 0}
  <section
    class="col-span-12 grid content-start gap-6 bg-card px-3.5 pbs-4 pbe-4"
    aria-labelledby={isOwn ? `${uid}-title` : undefined}
  >
    {#if isOwn}
      {const governments = $derived(tiers.flatMap((tier) => tier.governments))}
      <div class="grid gap-1">
        <h2
          id="{uid}-title"
          class="text-2xl/tight font-[760] tracking-[-0.015em] text-balance"
        >
          {m.local_section({ place: local.name })}
        </h2>
        <p class="text-[0.9375rem] text-pretty max-inline-[64ch]">
          {m.local_lede({
            governments: m.local_governments({ count: governments.length }),
            contests: m.race_contests({
              count: governments.flatMap((government) => government.contests)
                .length,
            }),
          })}
        </p>
      </div>
    {/if}
    {#each tiers as tier (tier.kind)}
      {const isOpen = $derived(unfolded.has(tier.kind))}
      <div class="grid content-start gap-3">
        <h3 class="text-lg/tight font-[740] tracking-[-0.012em]">
          {TIERS[tier.kind]()}
        </h3>
        <div class="columns-[24rem] gap-x-8">
          {#each isOpen || tier.governments.length <= 14 ? tier.governments : tier.governments.slice(0, 12) as government (government.path ?? government.name)}
            <div class="grid break-inside-avoid content-start gap-1 pbe-5">
              <h4 class="text-sm font-semibold">
                {#if government.path == null}
                  {government.name}
                {:else}
                  <a
                    class="underline decoration-transparent underline-offset-4 hover:decoration-foreground"
                    href={resolve("/[...path]", { path: government.path })}
                    >{government.name}</a
                  >
                {/if}
              </h4>
              <ul class="grid text-sm">
                {#each government.contests as contest, index (index)}
                  {const final = $derived(
                    contest.runoff?.isWon === true ? contest.runoff : null,
                  )}
                  {const shown = $derived(final ?? contest)}
                  <li
                    class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-0.5 border-be border-border/60 py-1.5 last:border-be-0 pointer-coarse:py-2"
                  >
                    {#if contest.path == null}
                      <span>{contest.name}</span>
                    {:else}
                      <Preview
                        class="underline decoration-border underline-offset-2 hover:decoration-foreground"
                        path={contest.path}>{contest.name}</Preview
                      >
                    {/if}
                    {#if shown.leader == null}
                      <span class="text-[0.8125rem] text-muted-foreground"
                        >{m.no_results()}</span
                      >
                    {:else}
                      <span
                        class="flex items-baseline gap-2 justify-self-end text-end"
                      >
                        {#if shown.person == null}
                          <span
                            class={cn("text-[0.8125rem]", {
                              "font-semibold": shown.isWon,
                              "text-muted-foreground": !shown.isWon,
                            })}>{shown.leader}</span
                          >
                        {:else}
                          <Name
                            class={cn("text-[0.8125rem]", {
                              "font-semibold": shown.isWon,
                              "text-muted-foreground": !shown.isWon,
                            })}
                            label={shown.leader}
                            person={shown.person}
                          />
                        {/if}
                        {#if shown.share != null}
                          <span
                            class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                            >{percent(shown.share, 1)}</span
                          >
                        {/if}
                      </span>
                    {/if}
                    {#if contest.runoff != null && contest.share != null}
                      <span class="col-span-2 text-xs text-muted-foreground">
                        {#if final == null}
                          {m.wards_pending({
                            share: percent(contest.share, 1),
                            round: round(contest.runoff),
                          })}
                        {:else if final.person != null && final.person === contest.person}
                          {m.wards_kept({
                            share: percent(contest.share, 1),
                            round: round(contest.runoff),
                            final: percent(final.share, 1),
                          })}
                        {:else}
                          {m.wards_took({
                            round: round(contest.runoff),
                            final: percent(final.share, 1),
                            name: contest.leader ?? "",
                            share: percent(contest.share, 1),
                          })}
                        {/if}
                      </span>
                    {/if}
                  </li>
                {/each}
              </ul>
            </div>
          {/each}
        </div>
        {#if tier.governments.length > 14}
          <button
            type="button"
            class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
            aria-expanded={isOpen}
            onclick={() => {
              if (isOpen) {
                unfolded.delete(tier.kind);
              } else {
                unfolded.add(tier.kind);
              }
            }}
            >{isOpen
              ? m.regions_fewer()
              : m.regions_more({
                  count: count(tier.governments.length),
                })}</button
          >
        {/if}
      </div>
    {/each}
  </section>
{/if}
