<script lang="ts">
  import type { LiveOption } from "@ea/analytics/live";
  import type { Standing } from "#lib/live/standing.js";
  import Name from "#lib/components/name.svelte";
  import { count, percent } from "#lib/format.js";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";

  const { options, now }: { options: Array<LiveOption>; now: Standing } =
    $props();

  const rows = $derived(
    now.order.flatMap((index) => {
      const option = options[index];
      return option == null || option.isWriteIn
        ? []
        : [
            {
              option,
              votes: now.totals[index] ?? 0,
              share: now.total > 0 ? (now.totals[index] ?? 0) / now.total : 0,
            },
          ];
    }),
  );
  const first = $derived(rows[0]);
  const trailing = $derived(rows.slice(2));
  const trailingShare = $derived.by(() => {
    let total = 0;
    for (const row of trailing) {
      total += row.share;
    }
    return total;
  });
  const names = $derived(
    new Intl.ListFormat(getLocale(), { type: "conjunction" }).format(
      trailing.map((row) => surname(row.option)),
    ),
  );
</script>

<section class="grid gap-4" aria-labelledby="ranked-title">
  <div class="grid gap-1">
    <h2 id="ranked-title" class="text-[0.8125rem] font-semibold">
      {m.night_rank_title()}
    </h2>
    <p class="text-sm text-pretty text-muted-foreground max-inline-[64ch]">
      {m.night_rank_body()}
    </p>
  </div>
  <ol class="grid gap-2 max-inline-160">
    {#each rows as row (row.option.id)}
      <li
        class="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_auto] items-center gap-3 text-[0.8125rem]"
      >
        <Name
          label={row.option.label}
          person={row.option.person}
          hasSwatch={false}
          class="truncate font-semibold"
        />
        <span
          class="relative block overflow-hidden rounded-xs bg-muted block-2.5"
          aria-hidden="true"
        >
          <span
            class="absolute inset-y-0 inset-s-0 transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
            style:inline-size="{row.share * 100}%"
            style:background-color={tone(row.option)}
          ></span>
          <span
            class="absolute inset-y-0 inset-s-1/2 -translate-x-1/2 bg-background inline-0.5"
          ></span>
        </span>
        <span class="text-end font-figures tabular-nums min-inline-24">
          <span class="font-semibold">{percent(row.share)}</span>
          <span class="text-xs text-muted-foreground">{count(row.votes)}</span>
        </span>
      </li>
    {/each}
  </ol>
  <p class="text-[0.8125rem] text-pretty max-inline-[64ch]">
    {#if first != null}
      {m.night_rank_short({
        name: surname(first.option),
        gap: lead(Math.max(0, 0.5 - first.share)),
      })}
    {/if}
    {#if trailing.length === 1}
      {m.night_rank_trailing_one({
        name: names,
        share: percent(trailingShare),
      })}
    {:else if trailing.length > 1}
      {m.night_rank_trailing({ names, share: percent(trailingShare) })}
    {/if}
  </p>
</section>
