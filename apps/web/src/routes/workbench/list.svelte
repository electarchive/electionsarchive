<script lang="ts">
  import { count, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { Contest, Desk, Entry } from "./desk.svelte.js";

  const {
    contest,
    desk,
    focus,
    kind,
  }: {
    contest: Contest;
    desk: Desk;
    focus: Entry | null;
    kind: "closest" | "movers" | "flips";
  } = $props();

  const LIMIT = 60;

  const legend = $derived(contest.places?.legend ?? []);
  const at = $derived(legend.findIndex((entry) => entry.key === focus?.key));
  const all = $derived(contest.places?.rows ?? []);
  const rows = $derived.by(() => {
    const found: Array<{
      name: string;
      valid: number;
      leader: Entry | null;
      prior: Entry | null;
      value: number;
    }> = [];
    for (const row of all) {
      const leader = row.leader == null ? null : (legend[row.leader] ?? null);
      const prior = row.prior == null ? null : (legend[row.prior] ?? null);
      if (kind === "closest") {
        if (row.margin != null) {
          found.push({
            name: row.name,
            valid: row.valid,
            leader,
            prior,
            value: row.margin,
          });
        }
      } else if (kind === "movers") {
        const share = row.shares[at];
        const earlier = row.earlier?.[at];
        if (share != null && earlier != null) {
          found.push({
            name: row.name,
            valid: row.valid,
            leader,
            prior,
            value: share - earlier,
          });
        }
      } else if (leader != null && prior != null && leader.key !== prior.key) {
        found.push({
          name: row.name,
          valid: row.valid,
          leader,
          prior,
          value: row.margin ?? 0,
        });
      }
    }
    return found.toSorted((a, b) =>
      kind === "closest"
        ? a.value - b.value
        : kind === "movers"
          ? Math.abs(b.value) - Math.abs(a.value)
          : b.valid - a.valid,
    );
  });
  const shown = $derived(rows.slice(0, LIMIT));
  const widest = $derived.by(() => {
    let most = 0.001;
    for (const row of shown) {
      most = Math.max(most, Math.abs(row.value));
    }
    return most;
  });
  const flipped = $derived.by(() => {
    if (kind !== "flips") {
      return [];
    }
    const tally = new Map<string, { from: Entry; to: Entry; count: number }>();
    for (const row of rows) {
      if (row.leader == null || row.prior == null) {
        continue;
      }
      const key = `${row.prior.key}>${row.leader.key}`;
      const entry = tally.get(key);
      if (entry == null) {
        tally.set(key, { from: row.prior, to: row.leader, count: 1 });
      } else {
        entry.count += 1;
      }
    }
    return [...tally.values()].toSorted((a, b) => b.count - a.count);
  });
  const isCompared = $derived(all.some((row) => row.earlier != null));
</script>

{#if kind !== "closest" && !isCompared}
  <p class="text-[0.75em] text-muted-foreground">
    {m.workbench_scatter_needs()}
  </p>
{:else if shown.length === 0}
  <p class="text-[0.75em] text-muted-foreground">
    {kind === "flips" ? m.workbench_list_none_flips() : m.workbench_list_none()}
  </p>
{:else}
  <div class="grid content-start gap-1.5">
    {#if kind === "flips"}
      <p class="flex flex-wrap gap-x-3 gap-y-0.5 text-[0.75em]">
        <span class="font-semibold"
          >{m.workbench_list_flipped({
            count: count(rows.length),
            total: count(all.length),
          })}</span
        >
        {#each flipped.slice(0, 3) as entry (`${entry.from.key}>${entry.to.key}`)}
          <span class="flex items-center gap-1 text-muted-foreground">
            <span
              class="rounded-full block-2 inline-2"
              style:background-color={swatch(entry.from.color, entry.from.key)}
              aria-hidden="true"
            ></span>
            <span
              class="rounded-full block-2 inline-2"
              style:background-color={swatch(entry.to.color, entry.to.key)}
              aria-hidden="true"
            ></span>
            <span class="sr-only"
              >{entry.from.short ?? entry.from.label}, {entry.to.short ??
                entry.to.label}</span
            >
            <span class="font-figures tabular-nums">{entry.count}</span>
          </span>
        {/each}
      </p>
    {/if}
    <ol class="grid text-[0.8125em]">
      {#each shown as row (row.name)}
        {const isPicked = $derived(desk.picked.has(row.name))}
        {const tone = $derived(
          kind === "movers"
            ? swatch(focus?.color ?? null, focus?.key ?? "")
            : swatch(row.leader?.color ?? null, row.leader?.key ?? ""),
        )}
        <li>
          <button
            type="button"
            class={cn(
              "grid grid-cols-[minmax(0,1fr)_4.5em_auto] items-center gap-x-2 border-be border-border/60 px-1 py-0.5 text-start inline-full",
              { "bg-accent": isPicked, "hover:bg-muted": !isPicked },
              { "bg-muted": desk.hovered === row.name && !isPicked },
            )}
            aria-pressed={isPicked}
            onclick={() => desk.toggle(row.name)}
            onpointerenter={() => (desk.hovered = row.name)}
            onpointerleave={() => (desk.hovered = null)}
          >
            <span class="flex items-center gap-1.5 min-inline-0">
              {#if kind === "flips" && row.prior != null}
                <span
                  class="shrink-0 rounded-full block-2 inline-2"
                  style:background-color={swatch(
                    row.prior.color,
                    row.prior.key,
                  )}
                  aria-hidden="true"
                ></span>
              {/if}
              <span
                class="shrink-0 rounded-full block-2 inline-2"
                style:background-color={swatch(
                  row.leader?.color ?? null,
                  row.leader?.key ?? "",
                )}
                aria-hidden="true"
              ></span>
              <span class="truncate font-semibold">{row.name}</span>
            </span>
            <span class="relative block-1.5" aria-hidden="true">
              {#if kind === "movers"}
                <span
                  class="absolute inset-y-0 inset-s-1/2 bg-foreground/30 inline-px"
                ></span>
                <span
                  class={cn("absolute inset-y-0", {
                    "opacity-50": row.value < 0,
                  })}
                  style:inset-inline-start={row.value < 0
                    ? `${50 - (Math.abs(row.value) / widest) * 50}%`
                    : "50%"}
                  style:inline-size="{(Math.abs(row.value) / widest) * 50}%"
                  style:background-color={tone}
                ></span>
              {:else}
                <span
                  class="absolute inset-y-0 inset-s-0 opacity-80"
                  style:inline-size="{(Math.abs(row.value) / widest) * 100}%"
                  style:background-color={tone}
                ></span>
              {/if}
            </span>
            <span
              class="text-end font-figures font-semibold tabular-nums min-inline-[5ch]"
              >{points(row.value)}</span
            >
          </button>
        </li>
      {/each}
    </ol>
    {#if rows.length > LIMIT}
      <p class="text-[0.6875em] text-muted-foreground">
        {m.workbench_list_more({ count: count(rows.length - LIMIT) })}
      </p>
    {/if}
  </div>
{/if}
