<script lang="ts">
  import { resolve } from "$app/paths";
  import type { Counted } from "#lib/server/counted.js";
  import { count } from "#lib/format.js";
  import { dated } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const { live }: { live: Counted } = $props();

  const uid = $props.id();
  const href = $derived(
    resolve("/live/[...path]", { path: live.path.replace(/^live\//u, "") }),
  );
  const isShown = $derived(live.at != null);
</script>

<section
  class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5"
  aria-labelledby="{uid}-title"
>
  <div class="grid gap-1">
    <h2 id="{uid}-title" class="text-[0.8125rem] font-bold">
      {m.counting_title()}
    </h2>
    {#if live.tally != null}
      <p class="text-sm text-pretty text-muted-foreground">
        {m.counting_contest({
          publisher: live.tally.publisher,
          time: dated(live.tally.at),
        })}
        {#if live.tally.reporting != null && live.tally.expected != null}
          {m.counting_reporting({
            reporting: count(live.tally.reporting),
            expected: count(live.tally.expected),
          })}
        {/if}
      </p>
    {:else if isShown}
      <p class="text-sm text-pretty text-muted-foreground">
        {(live.contests === 1 ? m.counting_election_one : m.counting_election)({
          count: count(live.contests),
          time: dated(live.at),
        })}
      </p>
    {:else}
      <p class="text-sm text-pretty text-muted-foreground">
        {m.counting_admin()}
      </p>
    {/if}
  </div>
  {#if live.tally != null && live.tally.rows.length > 0}
    <table class="text-[0.9375rem] tabular-nums max-inline-160">
      <thead class="text-xs text-muted-foreground">
        <tr class="border-be border-border">
          <th class="py-1.5 text-start font-semibold" scope="col"
            >{m.col_option()}</th
          >
          <th class="py-1.5 text-end font-semibold" scope="col"
            >{m.col_votes()}</th
          >
        </tr>
      </thead>
      <tbody class="divide-y divide-border">
        {#each live.tally.rows as row, index (index)}
          <tr>
            <th class="py-1.5 text-start font-semibold" scope="row">
              {row.label}
              {#if row.party != null}
                <span class="font-normal text-muted-foreground"
                  >{row.party}</span
                >
              {/if}
            </th>
            <td class="py-1.5 text-end">{count(row.votes)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
  <p>
    <a
      class="text-[0.9375rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
      {href}>{m.counting_link()}</a
    >
  </p>
</section>
