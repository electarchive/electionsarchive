<script lang="ts">
  import Tone from "#routes/coverage/tone.svelte";
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import {
    CATEGORY_LABELS,
    CATEGORY_REASONS,
    gapSpan,
    GRAIN_LABELS,
    levelLabel,
  } from "#lib/coverage.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";

  const { id }: { id: string } = $props();

  const uid = $props.id();
  const known = createQuery(() =>
    orpc.coverage.known.queryOptions({ input: { id } }),
  );
  const groups = $derived(
    [
      ...Map.groupBy(
        known.data ?? [],
        (entry) =>
          `${gapSpan({ ...entry, state: null, label: null })}|${entry.level}`,
      ).values(),
    ].flatMap((entries) => {
      const first = entries[0];
      return first == null
        ? []
        : [
            {
              key: `${first.from}-${first.to}-${first.level}`,
              years: gapSpan({ ...first, state: null, label: null }),
              office:
                first.level === ""
                  ? m.polity_known_all()
                  : levelLabel(first.polity, first.level),
              entries,
            },
          ];
    }),
  );
  const polity = $derived(known.data?.[0]?.polity ?? null);
</script>

{#if groups.length > 0}
  <section
    class="col-span-12 grid content-start gap-2.5 bg-card px-3.5 py-3"
    aria-labelledby="{uid}-known"
  >
    <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <h2 id="{uid}-known" class="text-[0.8125rem] font-bold">
        {m.polity_known_title()}
      </h2>
      {#if polity != null}
        <a
          href={resolve("/coverage/[cc]", { cc: polity })}
          class="text-[0.8125rem] text-muted-foreground underline underline-offset-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          >{m.polity_known_coverage()}</a
        >
      {/if}
    </div>
    <p class="text-[0.8125rem] text-muted-foreground max-inline-[70ch]">
      {m.polity_known_lede()}
    </p>
    <div class="grid">
      {#each groups as group (group.key)}
        <div
          class="grid gap-x-6 gap-y-1.5 border-bs border-border py-2.5 lg:grid-cols-[12rem_minmax(0,1fr)]"
        >
          <h3 class="text-[0.8125rem] leading-snug">
            <span class="font-semibold tabular-nums">{group.years}</span>
            <span class="block text-muted-foreground">{group.office}</span>
          </h3>
          <ul class="grid gap-2.5">
            {#each group.entries as entry, index (index)}
              {const grain = $derived(
                GRAIN_LABELS[entry.goal]?.() ?? entry.goal,
              )}
              {const where = $derived(
                [entry.state, entry.label].filter(
                  (part) =>
                    part != null &&
                    !grain.toLowerCase().includes(part.toLowerCase()),
                ),
              )}
              <li
                class="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-x-2 text-[0.8125rem] leading-snug"
              >
                {#if entry.category === "not_applicable"}
                  <span
                    aria-hidden="true"
                    class="mbs-0.5 block rounded-xs border border-muted-foreground/40 block-3 inline-3"
                  ></span>
                {:else}
                  <Tone
                    reason={CATEGORY_REASONS[entry.category]}
                    class="mbs-0.5 block rounded-xs block-3 inline-3"
                  />
                {/if}
                <div class="grid gap-0.5 min-inline-0">
                  <p>
                    <span class="font-semibold">{grain}</span>
                    <span class="text-muted-foreground"
                      >{[CATEGORY_LABELS[entry.category](), ...where].join(
                        ", ",
                      )}</span
                    >
                  </p>
                  <p class="max-inline-[75ch]">{entry.note}</p>
                  {#if entry.category !== "not_applicable" && entry.count > 0}
                    <p class="text-xs tabular-nums">
                      {m.polity_known_races({ count: count(entry.count) })}
                    </p>
                    <p class="text-xs">
                      {const dates = $derived(
                        entry.elections.map((election) => election.date),
                      )}
                      {const isSameDay = $derived(
                        dates.length > 1 && new Set(dates).size === 1,
                      )}
                      {const repeated = $derived(
                        new Set(
                          dates.filter((day, at) => dates.indexOf(day) !== at),
                        ),
                      )}
                      {#each entry.elections as election, at (election.path)}
                        <a
                          href={resolve("/[...path]", { path: election.path })}
                          class="tabular-nums underline underline-offset-2 hover:text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring"
                          >{isSameDay && election.place != null
                            ? election.place
                            : election.date == null
                              ? election.path
                              : repeated.has(election.date) &&
                                  election.place != null
                                ? `${date(election.date)} (${election.place})`
                                : date(election.date)}</a
                        >{at < entry.elections.length - 1 ? ", " : " "}
                      {/each}
                      {#if entry.more > 0}
                        <span class="text-muted-foreground"
                          >{m.polity_known_more({
                            count: count(entry.more),
                          })}</span
                        >
                      {/if}
                    </p>
                  {/if}
                </div>
              </li>
            {/each}
          </ul>
        </div>
      {/each}
    </div>
  </section>
{/if}
