<script lang="ts">
  import Seats from "#routes/[...path]/seats.svelte";
  import { m } from "#lib/paraglide/messages.js";
  import type { Contest } from "./desk.svelte.js";

  const {
    contest,
    span,
  }: {
    contest: Contest;
    span: { from: string; to: string; forward: boolean } | null;
  } = $props();

  const change = $derived.by(() => {
    if (span == null || contest.chamber == null) {
      return null;
    }
    const moves = contest.rows.flatMap((row) => {
      const before = span.forward ? row.current : row.earlier;
      const after = span.forward ? row.earlier : row.current;
      return before?.seats == null
        ? []
        : [[row.key, (after?.seats ?? 0) - before.seats] as const];
    });
    return moves.length === 0 ? null : Object.fromEntries(moves);
  });
</script>

{#if contest.chamber == null}
  <p class="text-[0.75em] text-muted-foreground">
    {m.workbench_coalition_none()}
  </p>
{:else}
  <Seats
    parties={contest.chamber.parties}
    places={contest.chamber.places}
    here={contest.chamber.here}
    place={contest.scope.name}
    link={contest.chamber.link}
    size={contest.chamber.size}
    body={contest.chamber.body}
    {change}
  />
{/if}
