<script lang="ts">
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    summary,
    point,
    room,
  }: {
    summary: {
      name: string;
      kind: string;
      valid: number;
      rows: Array<{
        key: string;
        label: string;
        tone: string;
        share: number;
        change: number | null;
      }>;
    };
    point: { x: number; y: number };
    room: { width: number; height: number };
  } = $props();

  let width = $state(0);
  let height = $state(0);
  const x = $derived(
    point.x + 16 + width > room.width - 8 ? point.x - 16 - width : point.x + 16,
  );
  const y = $derived(
    Math.min(Math.max(8, point.y - height / 2), room.height - height - 8),
  );
  const rows = $derived(summary.rows.slice(0, 3));
  const lead = $derived(
    (summary.rows[0]?.share ?? 0) - (summary.rows[1]?.share ?? 0),
  );
</script>

<div
  class="pointer-events-none absolute inset-s-0 inset-bs-0 z-30 grid gap-1.5 rounded-sm bg-popover p-2.5 text-[0.8125rem] shadow-menu ring-1 ring-border inline-60"
  style:translate="{x}px {y}px"
  bind:clientWidth={width}
  bind:clientHeight={height}
  aria-live="polite"
>
  <div class="grid">
    <span class="truncate font-semibold">{summary.name}</span>
    <span class="flex gap-2 text-xs text-muted-foreground">
      <span class="truncate">{summary.kind}</span>
      {#if summary.valid > 0}
        <span class="ms-auto shrink-0 font-figures tabular-nums"
          >{m.atlas_votes({ votes: count(summary.valid) })}</span
        >
      {/if}
    </span>
  </div>
  <ul class="grid gap-0.5">
    {#each rows as row, index (row.key)}
      <li class="flex items-center gap-2">
        <span
          class="shrink-0 rounded-xs block-2.5 inline-2.5"
          style:background={row.tone}
        ></span>
        <span class="flex-1 truncate">{row.label}</span>
        {#if index === 0 && summary.rows.length > 1}
          <span class="font-figures text-xs text-muted-foreground tabular-nums"
            >{points(lead)}</span
          >
        {/if}
        <span class="font-figures font-semibold tabular-nums"
          >{percent(row.share)}</span
        >
      </li>
    {/each}
  </ul>
</div>
