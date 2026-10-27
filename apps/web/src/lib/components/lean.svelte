<script lang="ts">
  import { cn } from "#lib/utils.js";
  import { count, definite, plural } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  let {
    sides,
    margin,
    before = null,
    marks,
    siblings,
    rank,
    noun,
    place,
    isAbroad = false,
    year = null,
    class: className = "",
  }: {
    sides: [{ label: string; tone: string }, { label: string; tone: string }];
    margin: number;
    before?: number | null;
    marks: Array<{ name: string; margin: number }>;
    siblings: Array<number>;
    rank: number;
    noun: string;
    place: string;
    isAbroad?: boolean;
    year?: number | null;
    class?: string;
  } = $props();

  const reach = $derived.by(() => {
    let widest = Math.max(Math.abs(margin), Math.abs(before ?? 0));
    for (const mark of marks) {
      widest = Math.max(widest, Math.abs(mark.margin));
    }
    for (const value of siblings) {
      widest = Math.max(widest, Math.abs(value));
    }
    return Math.min(1, Math.max(0.1, Math.ceil(widest * 10) / 10));
  });
  const ticks = $derived(
    siblings.map((value) => `M${(value / reach).toFixed(4)} 0V1`).join(""),
  );
  const side = $derived(margin >= 0 ? sides[0] : sides[1]);
  const shift = $derived(before == null ? null : margin - before);

  function x(value: number) {
    return `${50 + (value / reach) * 50}%`;
  }
</script>

<div class={cn("grid grid-cols-[minmax(0,1fr)] gap-2", className)}>
  <div class="grid grid-cols-[minmax(0,1fr)] gap-1" aria-hidden="true">
    <div class="relative block-7">
      <svg
        class="absolute inset-0 size-full overflow-visible"
        viewBox="-1 0 2 1"
        preserveAspectRatio="none"
      >
        <path
          d="M-1 0.5H1"
          class="stroke-border"
          vector-effect="non-scaling-stroke"
        />
        <path
          d="M0 0V1"
          class="stroke-border"
          stroke-dasharray="2 2"
          vector-effect="non-scaling-stroke"
        />
        <path
          d={ticks}
          class="stroke-muted-foreground/35"
          vector-effect="non-scaling-stroke"
        />
      </svg>
      {#each marks as mark, index (mark.name)}
        <span
          class={cn("absolute inset-y-0 -translate-x-1/2 inline-0.5", {
            "bg-foreground": index === 0,
            "bg-muted-foreground": index > 0,
          })}
          style:left={x(mark.margin)}
        ></span>
      {/each}
      {#if before != null}
        <span
          class="absolute inset-bs-1/2 -translate-y-1/2 bg-foreground/45 block-0.5"
          style:left={x(Math.min(before, margin))}
          style:width="{(Math.abs(margin - before) / reach) * 50}%"
        ></span>
        <span
          class="absolute inset-bs-1/2 -translate-1/2 rounded-full bg-card ring-[1.5px] ring-foreground/60 block-2 inline-2"
          style:left={x(before)}
        ></span>
      {/if}
      <span
        class="absolute inset-bs-1/2 -translate-1/2 rounded-full ring-2 ring-card block-2.5 inline-2.5"
        style:left={x(margin)}
        style:background-color={side.tone}
      ></span>
    </div>
    <div
      class="flex justify-between gap-2 font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
    >
      <span class="truncate"
        >{m.atlas_lead({
          name: sides[1].label,
          points: (reach * 100).toFixed(0),
        })}</span
      >
      <span class="shrink-0">{m.atlas_even()}</span>
      <span class="truncate text-end"
        >{m.atlas_lead({
          name: sides[0].label,
          points: (reach * 100).toFixed(0),
        })}</span
      >
    </div>
  </div>
  <ul class="grid grid-cols-[minmax(0,1fr)] gap-1 text-[0.8125rem]">
    {#each marks as mark, index (mark.name)}
      {const gap = $derived(margin - mark.margin)}
      <li class="flex items-center gap-2">
        <span
          class={cn("shrink-0 block-3 inline-0.5", {
            "bg-foreground": index === 0,
            "bg-muted-foreground": index > 0,
          })}
        ></span>
        <span class="flex-1 truncate text-muted-foreground"
          >{m.lean_versus({ place: definite(mark.name) })}</span
        >
        <span class="truncate font-figures font-semibold tabular-nums"
          >{Math.abs(gap) < 0.0005
            ? m.atlas_even()
            : m.atlas_lead({
                name: (gap >= 0 ? sides[0] : sides[1]).label,
                points: Math.abs(gap * 100).toFixed(1),
              })}</span
        >
      </li>
    {/each}
    {#if siblings.length > 1}
      <li class="flex items-center gap-2">
        <span
          class="shrink-0 bg-[repeating-linear-gradient(90deg,var(--muted-foreground)_0_1px,transparent_1px_3px)] opacity-50 block-3 inline-2.5"
        ></span>
        <span class="text-pretty text-muted-foreground"
          >{isAbroad
            ? m.lean_rank_abroad({
                rank,
                name: side.label,
                count: count(siblings.length),
                nouns: plural(noun),
              })
            : m.lean_rank({
                rank,
                name: side.label,
                count: count(siblings.length),
                nouns: plural(noun),
                place: definite(place),
              })}</span
        >
      </li>
    {/if}
    {#if shift != null && year != null}
      <li class="flex items-center gap-2">
        <span
          class="shrink-0 rounded-full ring-[1.5px] ring-foreground/60 block-2 inline-2"
        ></span>
        <span class="text-muted-foreground"
          >{m.atlas_shift({
            name: (shift >= 0 ? sides[0] : sides[1]).label,
            points: Math.abs(shift * 100).toFixed(1),
            year,
          })}</span
        >
      </li>
    {/if}
  </ul>
</div>
