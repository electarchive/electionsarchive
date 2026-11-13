<script lang="ts">
  import { date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  type Status = "held" | "queued" | "blocked" | "out_of_scope" | "open";

  const {
    classes,
  }: {
    classes: Array<{
      key: string;
      name: string;
      tier: string;
      tierName: string;
      from: number | null;
      to: number | null;
      status: Status;
      reason: string | null;
      share: number | null;
      next: string | null;
    }>;
  } = $props();

  const LABELS: Record<Status, () => string> = {
    held: m.coverage_class_held,
    queued: m.coverage_class_queued,
    blocked: m.coverage_class_blocked,
    out_of_scope: m.coverage_class_out_of_scope,
    open: m.coverage_class_open,
  };

  const tiers = $derived.by(() => {
    const grouped = new Map<string, { name: string; items: typeof classes }>();
    for (const row of classes) {
      const tier = grouped.get(row.tier) ?? { name: row.tierName, items: [] };
      tier.items.push(row);
      grouped.set(row.tier, tier);
    }
    return [...grouped];
  });
</script>

<div class="grid gap-4">
  {#each tiers as [key, tier] (key)}
    <section aria-labelledby="class-tier-{key}" class="grid gap-1.5">
      <h3 id="class-tier-{key}" class="text-sm font-bold">{tier.name}</h3>
      <ul class="grid gap-px overflow-clip rounded-sm border bg-border">
        {#each tier.items as row (row.key)}
          <li
            class="grid gap-x-3 gap-y-0.5 bg-card px-3 py-2 text-sm sm:grid-cols-[minmax(0,1fr)_6.5rem_7rem_minmax(0,11rem)] sm:items-baseline"
          >
            <span class="font-semibold text-pretty"
              >{row.name}{#if row.from != null || row.to != null}<span
                  class="font-normal text-muted-foreground tabular-nums"
                  >{" "}({row.from ?? ""}–{row.to ?? ""})</span
                >{/if}</span
            >
            <span
              class={cn("text-xs font-semibold", {
                "text-(--yes)": row.status === "held",
                "text-(--no)": row.status === "blocked",
                "text-muted-foreground":
                  row.status === "out_of_scope" || row.status === "open",
              })}>{LABELS[row.status]()}</span
            >
            <span class="text-xs text-muted-foreground tabular-nums"
              >{row.share == null
                ? ""
                : m.coverage_class_counted({
                    share: percent(row.share, 0),
                  })}</span
            >
            <span class="text-xs text-muted-foreground tabular-nums"
              >{row.next == null
                ? ""
                : m.coverage_class_next({ date: date(row.next) })}</span
            >
            {#if row.reason != null}
              <p
                class="text-xs text-muted-foreground max-inline-[72ch] sm:col-span-full"
              >
                {row.reason}
              </p>
            {/if}
          </li>
        {/each}
      </ul>
    </section>
  {/each}
</div>
