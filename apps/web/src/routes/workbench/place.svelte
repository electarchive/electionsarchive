<script lang="ts">
  import { resolve } from "$app/paths";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { Contest, Desk } from "./desk.svelte.js";

  const { contest, desk }: { contest: Contest; desk: Desk } = $props();

  let last = $state<string | null>(null);

  const rows = $derived(contest.places?.rows ?? []);
  const legend = $derived(contest.places?.legend ?? []);
  const overall = $derived(
    new Map(
      contest.rows.map((row) => [row.key, row.current?.share ?? null] as const),
    ),
  );

  $effect(() => {
    const name = desk.hovered;
    if (name != null && rows.some((row) => row.name === name)) {
      last = name;
    }
  });

  const found = $derived(
    rows.find((row) => row.name === last) ??
      rows.find((row) => desk.picked.has(row.name)) ??
      null,
  );
  const lines = $derived(
    found == null
      ? []
      : legend
          .map((entry, index) => {
            const share = found.shares[index] ?? 0;
            const earlier = found.earlier?.[index] ?? null;
            const whole = overall.get(entry.key) ?? null;
            return {
              ...entry,
              share,
              earlier,
              change: earlier == null ? null : share - earlier,
              gap: whole == null ? null : share - whole,
            };
          })
          .filter((line, index) => index < 3 || line.share >= 0.01)
          .toSorted((a, b) => b.share - a.share)
          .slice(0, 8),
  );
  const leader = $derived(
    found?.leader == null ? null : (legend[found.leader] ?? null),
  );
  const prior = $derived(
    found?.prior == null ? null : (legend[found.prior] ?? null),
  );
  const isFlip = $derived(
    leader != null && prior != null && leader.key !== prior.key,
  );
</script>

{#if found == null}
  <p class="text-[0.75em] text-muted-foreground">{m.workbench_place_hint()}</p>
{:else}
  <div class="grid content-start gap-2">
    <div class="flex items-start justify-between gap-2">
      <div class="grid gap-0.5 min-inline-0">
        <p class="truncate text-[1.125em] leading-tight font-[720]">
          {#if found.path == null}
            {found.name}
          {:else}
            <a
              class="hover:underline"
              href={resolve("/[...path]", { path: found.path })}>{found.name}</a
            >
          {/if}
        </p>
        <p
          class="font-figures text-[0.75em] text-muted-foreground tabular-nums"
        >
          {m.workbench_results_total({
            count: count(found.valid),
          })}{#if found.margin != null}, {m.workbench_results_margin({
              points: points(found.margin),
            })}{/if}
        </p>
      </div>
      {#if isFlip && leader != null && prior != null}
        <span
          class="flex shrink-0 items-center gap-1 rounded-xs border border-border px-1.5 py-0.5 text-[0.6875em] font-semibold"
        >
          <span
            class="rounded-full block-2 inline-2"
            style:background-color={swatch(prior.color, prior.key)}
            aria-hidden="true"
          ></span>
          {m.workbench_place_flip()}
          <span
            class="rounded-full block-2 inline-2"
            style:background-color={swatch(leader.color, leader.key)}
            aria-hidden="true"
          ></span>
        </span>
      {/if}
    </div>
    <ul class="grid gap-1.5 text-[0.8125em]">
      {#each lines as line (line.key)}
        {const color = $derived(swatch(line.color, line.key))}
        <li class="grid gap-0.5">
          <span
            class="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-x-2"
          >
            <span
              class={cn("truncate", {
                "font-semibold": line.key === leader?.key,
              })}
              >{line.person == null
                ? (line.short ?? line.label)
                : line.label}</span
            >
            <span class="font-figures font-semibold tabular-nums"
              >{percent(line.share)}</span
            >
            <span
              class={cn(
                "text-end font-figures text-[0.875em] tabular-nums min-inline-[5ch]",
                {
                  "text-muted-foreground":
                    line.change == null || line.change < 0,
                },
              )}>{line.change == null ? "" : points(line.change)}</span
            >
          </span>
          <span class="relative block-1.5" aria-hidden="true">
            <span class="absolute inset-0 bg-muted"></span>
            {#if line.earlier != null}
              <span
                class="absolute inset-y-0 inset-s-0 border opacity-60"
                style:inline-size="{line.earlier * 100}%"
                style:border-color={color}
              ></span>
            {/if}
            <span
              class="absolute inset-y-px inset-s-0"
              style:inline-size="{line.share * 100}%"
              style:background-color={color}
            ></span>
            {#if line.gap != null}
              <span
                class="absolute -inset-y-0.5 bg-foreground inline-px"
                style:inset-inline-start="{(line.share - line.gap) * 100}%"
              ></span>
            {/if}
          </span>
        </li>
      {/each}
    </ul>
    <p class="text-[0.6875em] text-muted-foreground">
      {m.workbench_place_key()}
    </p>
  </div>
{/if}
