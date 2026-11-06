<script lang="ts">
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    head,
  }: { head: { name: string; date: string | null; status: string } } = $props();

  const STATUSES: Record<string, () => string> = {
    scheduled: m.election_scheduled,
    postponed: m.election_postponed,
    canceled: m.election_canceled,
    annulled: m.election_annulled,
  };
</script>

<div class="grid content-start gap-1.5 min-inline-0">
  <h1 class="title">
    {head.name}
  </h1>
  <p
    class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem] text-muted-foreground"
  >
    <time datetime={head.date}>{date(head.date)}</time>
    {#if STATUSES[head.status] != null}
      <span class="highlighter px-1 font-semibold text-foreground"
        >{STATUSES[head.status]?.()}</span
      >
    {/if}
  </p>
</div>
