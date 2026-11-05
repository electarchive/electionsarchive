<script lang="ts">
  import type { LiveOption } from "@ea/analytics/live";
  import type { Summary } from "#lib/live/summary.js";
  import BandLabel from "#lib/components/live/band-label.svelte";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import Face from "#lib/components/live/face.svelte";
  import Also from "#lib/components/live/state/also.svelte";
  import Name from "#lib/components/name.svelte";
  import * as HoverCard from "#lib/components/ui/hover-card/index.js";
  import { count, percent } from "#lib/format.js";
  import { lead, paint, surname, tone } from "#lib/live/format.js";
  import { lineup, useReach } from "#lib/live/lineup.js";
  import { liveLinks } from "#lib/live/links.js";
  import { odds } from "#lib/live/nation.js";
  import { bandOf, describe, isUncounted } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import { prior } from "./model.js";

  const {
    summary,
    held,
    flip,
    closes,
    clock,
    forecasts,
    title,
    limit = 6,
    isLinked = true,
  }: {
    summary: Summary;
    held: {
      party: string;
      partyId: string;
      color: string;
      person: string;
      name: string;
    } | null;
    flip: {
      flip: "flipped" | "leading";
      from: { short: string; color: string };
      to: { short: string; color: string };
    } | null;
    closes: number;
    clock: number;
    forecasts: boolean;
    title?: string;
    limit?: number;
    isLinked?: boolean;
  } = $props();

  const toLive = liveLinks();

  const race = $derived(summary.race);
  const last = $derived(prior(race));
  const headline = $derived(describe(summary, closes, clock));
  const band = $derived(bandOf(summary));
  const reach = useReach();
  const split = $derived(
    lineup(
      race,
      summary.totals,
      reach(),
      Math.max(limit, (race.seats ?? 1) + 1),
    ),
  );
  const rows = $derived(
    split.main.map((line) => ({
      option: line.option,
      votes: line.votes,
      share: line.share ?? 0,
      before: last?.shares[race.options.indexOf(line.option)] ?? null,
    })),
  );
  const scale = $derived(
    Math.max(0.5, ...rows.map((row) => Math.max(row.share, row.before ?? 0))),
  );
  const chance = $derived.by(() => {
    const favorite = summary.favorite;
    if (
      !forecasts ||
      favorite == null ||
      summary.chance == null ||
      band != null ||
      summary.estimate?.status !== "estimating"
    ) {
      return null;
    }
    return { name: surname(favorite), chance: odds(summary.chance) };
  });
  const lastMargin = $derived.by(() => {
    const option = last?.leader == null ? null : race.options[last.leader];
    return last == null || option == null
      ? null
      : `${surname(option)} +${lead(last.margin)}`;
  });

  const previous = $derived(
    (last?.shares ?? [])
      .flatMap((share, index) => {
        const option = race.options[index];
        return option == null || share <= 0 ? [] : [{ option, share }];
      })
      .toSorted((a, b) => b.share - a.share)
      .slice(0, 4),
  );

  const seats = $derived(race.seats ?? 1);

  function isDecided(option: LiveOption) {
    return band?.ids.includes(option.id) === true;
  }
</script>

<article class="grid grid-cols-[minmax(0,1fr)] gap-3" aria-label={race.title}>
  <header class="grid gap-1">
    <h3 class="text-[0.9375rem]/5 font-[720] tracking-tight text-balance">
      {#if isLinked}
        <a
          href={toLive(race.slug)}
          class="underline decoration-transparent underline-offset-3 transition-colors hover:decoration-foreground"
          >{title ?? race.title}</a
        >
      {:else}
        {title ?? race.title}
      {/if}
    </h3>
    <p class="font-serif text-[0.9375rem]/snug text-pretty">{headline}</p>
  </header>

  <div
    class={cn(
      "grid gap-3 transition-colors duration-700 motion-reduce:transition-none",
      { "-mx-3 rounded-sm band p-3": band != null },
    )}
    style:--party={band?.fill}
  >
    {#if band != null}
      <BandLabel
        {band}
        isMeasure={race.isMeasure ?? false}
        at={summary.projectedAt}
        class="text-xs"
      />
    {/if}
    <ol class="grid gap-2.5">
      {#each rows as row, rank (row.option.id)}
        {const color = $derived(paint(row.option, band))}
        {const isDim = $derived(band != null && !isDecided(row.option))}
        {const isStrong = $derived(
          band == null
            ? row.option.id === summary.leader?.id
            : isDecided(row.option),
        )}
        {#if seats > 1 && rank === seats && rows.length > seats}
          <li
            class="flex items-center gap-2 text-[0.6875rem] text-muted-foreground"
            aria-hidden="true"
          >
            <span class="flex-1 border-bs border-dashed border-current"></span>
            {m.night_seats_line({ count: count(seats) })}
            <span class="flex-1 border-bs border-dashed border-current"></span>
          </li>
        {/if}
        <li
          class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2.5"
        >
          {#if row.option.photo != null || row.option.person != null}
            <Face option={row.option} {color} class="block-9 inline-9" />
          {:else}
            <span
              class="block rounded-full block-3 inline-3"
              style:background-color={color}
            ></span>
          {/if}
          <span class="grid gap-1 min-inline-0">
            <span
              class="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 min-inline-0"
            >
              <Name
                label={row.option.label}
                person={row.option.person}
                hasSwatch={false}
                class={cn("leading-snug wrap-anywhere", {
                  "font-semibold": isStrong,
                  "text-muted-foreground": isDim,
                })}
              />
              {#if row.option.isIncumbent}
                <span
                  class="shrink-0 text-[0.6875rem] text-muted-foreground"
                  title={m.results_incumbent()}>{m.results_incumbent()}</span
                >
              {/if}
            </span>
            <span
              class="relative overflow-visible rounded-xs bg-muted block-1.5"
              aria-hidden="true"
            >
              <span
                class="absolute inset-y-0 inset-s-0 rounded-xs transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
                style:inline-size="{(row.share / scale) * 100}%"
                style:background-color={color}
              ></span>
              {#if row.before != null && row.before > 0}
                <span
                  class="absolute -inset-y-0.5 rounded-full bg-foreground/70 inline-0.5"
                  style:inset-inline-start="calc({(row.before / scale) * 100}% -
                  1px)"
                  title={m.night_tip_baseline({ margin: percent(row.before) })}
                ></span>
              {/if}
            </span>
            {#if row.option.party != null}
              <span class="truncate text-xs text-muted-foreground"
                ><Name
                  label={row.option.party.short}
                  party={row.option.party.id}
                  color={row.option.party.color}
                  hasSwatch={false}
                  class="hover:text-foreground"
                /></span
              >
            {/if}
          </span>
          {#if summary.total > 0}
            <span class="grid justify-items-end font-figures tabular-nums">
              <span
                class={cn("text-[0.9375rem] font-semibold", {
                  "text-muted-foreground": isDim,
                })}>{percent(row.share)}</span
              >
              <span class="text-xs text-muted-foreground"
                >{count(row.votes)}</span
              >
            </span>
          {:else if row.before != null && row.before > 0}
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              title={m.night_table_base()}>{percent(row.before)}</span
            >
          {/if}
        </li>
      {/each}
    </ol>
    <Also rest={split.rest} total={summary.total} />

    <div class={cn("grid gap-1.5", { hidden: isUncounted(summary) })}>
      <div
        class="relative overflow-hidden rounded-xs bg-muted block-1"
        aria-hidden="true"
      >
        <span
          class="absolute inset-y-0 inset-s-0 bg-foreground transition-[inline-size] duration-700 motion-reduce:transition-none"
          style:inline-size="{(summary.counted ?? 0) * 100}%"
        ></span>
      </div>
      <p
        class="flex flex-wrap justify-between gap-x-3 gap-y-0.5 text-xs text-muted-foreground"
      >
        <span>
          {#if summary.total === 0}
            {m.night_counted_none()}
          {:else if summary.counted != null}
            {m.night_counted_expected({ share: percent(summary.counted, 0) })}
          {:else}
            {m.night_votes({ count: count(summary.total) })}
          {/if}
        </span>
        {#if chance != null}
          <span class="font-semibold text-foreground"
            >{m.night_nation_chance(chance)}</span
          >
        {/if}
      </p>
    </div>
  </div>

  {#if lastMargin != null || held != null}
    <dl
      class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 border-bs pbs-2.5 text-xs"
    >
      {#if lastMargin != null}
        <dt class="text-muted-foreground">{m.night_table_base()}</dt>
        <dd class="flex justify-end min-inline-0">
          <HoverCard.Root openDelay={120} closeDelay={60}>
            <HoverCard.Trigger>
              {#snippet child({ props })}
                <a
                  {...props}
                  href={toLive(race.slug)}
                  class="truncate font-figures tabular-nums underline decoration-border decoration-dotted underline-offset-3 transition-colors hover:decoration-foreground focus-visible:decoration-foreground"
                  role={undefined}
                  aria-haspopup={undefined}
                  aria-expanded={undefined}>{lastMargin}</a
                >
              {/snippet}
            </HoverCard.Trigger>
            <HoverCard.Content
              collisionPadding={8}
              align="end"
              class="grid gap-2.5 inline-[min(17rem,calc(100vw-2rem))]"
            >
              <p class="text-xs font-semibold">{m.night_table_base()}</p>
              <ol class="grid gap-2">
                {#each previous as line (line.option.id)}
                  <li
                    class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-1 text-xs"
                  >
                    <span class="truncate"
                      >{line.option.party?.name ?? line.option.label}</span
                    >
                    <span class="font-figures font-semibold tabular-nums"
                      >{percent(line.share)}</span
                    >
                    <span
                      class="col-span-2 overflow-hidden rounded-xs bg-muted block-1"
                      ><span
                        class="block block-full"
                        style:inline-size="{line.share * 100}%"
                        style:background-color={tone(line.option)}
                      ></span></span
                    >
                  </li>
                {/each}
              </ol>
            </HoverCard.Content>
          </HoverCard.Root>
        </dd>
      {/if}
      {#if held != null}
        <dt class="text-muted-foreground">{m.livemap_held()}</dt>
        <dd class="flex items-center justify-end gap-1.5 text-end min-inline-0">
          <span class="truncate"
            ><Name label={held.name} person={held.person} hasSwatch={false} />
            (<Name
              label={held.party}
              party={held.partyId}
              color={held.color}
              hasSwatch={false}
            />)</span
          >
          {#if flip != null}
            <Flip flip={flip.flip} from={flip.from} to={flip.to} />
          {/if}
        </dd>
      {/if}
    </dl>
  {/if}
</article>
