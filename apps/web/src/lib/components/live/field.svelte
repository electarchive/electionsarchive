<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import CheckIcon from "@lucide/svelte/icons/check";
  import type { Standing } from "#lib/live/standing.js";
  import Name from "#lib/components/name.svelte";
  import { percent } from "#lib/format.js";
  import { tone } from "#lib/live/format.js";
  import { toward } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import MethodLink from "./method-link.svelte";

  const {
    options,
    now,
    estimate,
    winners,
  }: {
    options: Array<LiveOption>;
    now: Standing;
    estimate: LiveEstimate | null;
    winners: Array<string>;
  } = $props();

  const isLive = $derived(estimate?.status === "estimating" && !now.isFinal);
  const a = $derived(now.pair[0]);
  const b = $derived(now.pair[1]);
  const reported = (index: number) =>
    now.total > 0 ? (now.totals[index] ?? 0) / now.total : null;
  const others = $derived(
    now.total > 0
      ? 1 - ((now.totals[a] ?? 0) + (now.totals[b] ?? 0)) / now.total
      : 0,
  );

  const finals = $derived.by(() => {
    const found = new Map<number, { low: number; mid: number; high: number }>();
    if (estimate == null || !isLive) {
      return found;
    }
    const flip = toward(estimate, options[a]);
    const low = estimate.margin.low * flip;
    const high = estimate.margin.high * flip;
    const margin = {
      low: Math.min(low, high),
      mid: estimate.margin.mid * flip,
      high: Math.max(low, high),
    };
    const base = 1 - others;
    found.set(a, {
      low: (base + margin.low) / 2,
      mid: (base + margin.mid) / 2,
      high: (base + margin.high) / 2,
    });
    found.set(b, {
      low: (base - margin.high) / 2,
      mid: (base - margin.mid) / 2,
      high: (base - margin.low) / 2,
    });
    return found;
  });

  const rows = $derived(
    (now.total > 0
      ? now.order
      : [a, b, ...now.order.filter((index) => index !== a && index !== b)]
    ).slice(0, 6),
  );
  const ceiling = $derived.by(() => {
    let top = 0.1;
    for (const index of rows) {
      top = Math.max(top, reported(index) ?? 0, finals.get(index)?.high ?? 0);
    }
    return Math.min(1, Math.ceil((top + 0.04) * 10) / 10);
  });
  const ticks = $derived(
    Array.from(
      { length: Math.round(ceiling * 10) },
      (_, index) => (index + 1) / 10,
    ).filter((value) => value < ceiling),
  );
  const place = (value: number) =>
    `${(Math.max(0, Math.min(ceiling, value)) / ceiling) * 100}%`;
  const shareIn = $derived(
    isLive && estimate != null
      ? Math.min(0.99, estimate.shareIn.mid)
      : now.isFinal
        ? 1
        : null,
  );
</script>

<section class="grid gap-4" aria-labelledby="field-title">
  <div class="flex items-center gap-1.5">
    <h2 id="field-title" class="heading text-lg!">{m.night_field_title()}</h2>
    <MethodLink section="how" />
  </div>
  <table class="border-collapse text-[0.875rem] inline-full">
    <thead>
      <tr class="text-xs text-muted-foreground">
        <th scope="col" class="pbe-2 text-start font-normal"
          >{m.night_field_candidate()}</th
        >
        <th scope="col" class="pe-4 pbe-2 text-end font-normal"
          >{m.night_field_reported()}</th
        >
        <th scope="col" class="pe-4 pbe-2 text-end font-normal"
          >{m.night_field_final()}</th
        >
        <th scope="col" class="relative hidden pbe-2 font-normal sm:table-cell">
          <span class="sr-only">{m.night_field_axis()}</span>
          {#each ticks as tick (tick)}
            <span
              class="absolute inset-be-2 -translate-x-1/2 font-figures tabular-nums"
              style:left={place(tick)}
              aria-hidden="true">{percent(tick, 0)}</span
            >
          {/each}
        </th>
      </tr>
    </thead>
    <tbody>
      {#each rows as index (options[index]?.id ?? index)}
        {const option = $derived(options[index])}
        {const color = $derived(tone(option))}
        {const share = $derived(reported(index))}
        {const final = $derived(finals.get(index) ?? null)}
        {const isWinner = $derived(
          option != null && winners.includes(option.id),
        )}
        {const isPair = $derived(index === a || index === b)}
        <tr
          class={cn("border-bs border-border", {
            "text-muted-foreground": !isPair,
          })}
        >
          <th
            scope="row"
            class={cn(
              "ps-3 text-start font-normal shadow-[inset_3px_0_0_var(--edge)]",
              { "py-2": isPair, "py-1": !isPair },
            )}
            style:--edge={color}
          >
            <span class="flex flex-wrap items-baseline gap-x-2">
              <span
                class={cn("flex items-center gap-1.5", {
                  "font-semibold text-foreground": isPair,
                })}
              >
                <Name
                  label={option?.label ?? ""}
                  person={option?.person ?? null}
                  hasSwatch={false}
                />
                {#if isWinner}
                  <CheckIcon
                    class="block-4 inline-4"
                    style="color: {color}"
                    aria-label={m.night_winner()}
                  />
                {/if}
              </span>
              {#if option?.isAnswer !== true}
                <span class="text-xs text-muted-foreground"
                  >{#if option?.party == null}{m.night_no_party()}{:else}<Name
                      label={option.party.name}
                      party={option.party.id}
                      color={option.party.color}
                      hasSwatch={false}
                    />{/if}</span
                >
              {/if}
            </span>
          </th>
          <td
            class={cn("pe-4 text-end font-figures tabular-nums", {
              "py-2 text-base": isPair,
              "py-1": !isPair,
            })}>{share == null ? "–" : percent(share)}</td
          >
          <td
            class={cn("pe-4 text-end font-figures tabular-nums", {
              "py-2": isPair,
              "py-1": !isPair,
            })}
          >
            {#if final == null}
              <span class="text-muted-foreground">–</span>
            {:else}
              <span class="block text-base font-[760]" style:color
                >{percent(final.mid)}</span
              >
              <span
                class="block text-[0.6875rem] font-normal text-muted-foreground"
                >{m.night_field_range({
                  low: percent(final.low, 0),
                  high: percent(final.high, 0),
                })}</span
              >
            {/if}
          </td>
          <td
            class="relative hidden min-inline-64 sm:table-cell"
            aria-hidden="true"
          >
            <span class="absolute inset-x-0 inset-y-1 bg-muted/60"></span>
            {#each ticks as tick (tick)}
              <span
                class="absolute inset-y-1 bg-background inline-px"
                style:left={place(tick)}
              ></span>
            {/each}
            {#if final != null}
              <span
                class="absolute inset-bs-1/2 -translate-y-1/2 rounded-full opacity-30 transition-all duration-700 block-3 motion-reduce:transition-none"
                style:left={place(final.low)}
                style:width="calc({place(final.high)} - {place(final.low)})"
                style:background-color={color}
              ></span>
            {/if}
            {#if share != null}
              <span
                class="absolute inset-bs-1/2 -translate-1/2 rounded-full border-2 bg-background transition-[left] duration-700 block-2.5 inline-2.5 motion-reduce:transition-none"
                style:left={place(share)}
                style:border-color={color}
              ></span>
            {/if}
            {#if final != null}
              <span
                class="absolute inset-bs-1/2 -translate-1/2 rounded-full ring-2 ring-background transition-[left] duration-700 block-4 inline-4 motion-reduce:transition-none"
                style:left={place(final.mid)}
                style:background-color={color}
              ></span>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
  <p
    class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground"
  >
    <span class="flex items-center gap-1.5"
      ><span
        class="rounded-full border-2 border-muted-foreground block-2.5 inline-2.5"
      ></span>{m.night_field_legend_counted()}</span
    >
    <span class="flex items-center gap-1.5"
      ><span class="rounded-full bg-muted-foreground block-3 inline-3"
      ></span>{m.night_field_legend_final()}</span
    >
    <span class="flex items-center gap-1.5"
      ><span class="rounded-full bg-muted-foreground/30 block-2 inline-5"
      ></span>{m.night_dial_band()}</span
    >
  </p>

  {#if shareIn != null}
    <div class="grid gap-2 border-bs border-border pbs-4">
      <div class="flex items-center gap-1">
        <h3 class="text-sm font-semibold">{m.night_field_basis()}</h3>
        <MethodLink section="turnout" />
      </div>
      <dl
        class="grid gap-2 text-[0.8125rem] sm:grid-cols-[minmax(0,22rem)_minmax(0,28rem)_3.5rem] sm:items-center sm:gap-x-4"
      >
        <dt class="grid">
          <span class="font-semibold">{m.night_field_counted()}</span>
          <span class="text-xs text-muted-foreground"
            >{m.night_field_counted_hint()}</span
          >
        </dt>
        <dd
          class="relative overflow-hidden rounded-xs bg-muted block-3"
          aria-hidden="true"
        >
          <span
            class="absolute inset-y-0 inset-s-0 bg-foreground/70 transition-[inline-size] duration-700 motion-reduce:transition-none"
            style:inline-size="{shareIn * 100}%"
          ></span>
        </dd>
        <dd class="font-figures font-semibold tabular-nums sm:text-end">
          {percent(shareIn, 0)}
        </dd>
        <dt class="grid">
          <span class="font-semibold">{m.night_field_projected()}</span>
          <span class="text-xs text-muted-foreground"
            >{m.night_field_projected_hint()}</span
          >
        </dt>
        <dd
          class="relative overflow-hidden rounded-xs bg-muted block-3"
          aria-hidden="true"
        >
          <span
            class="absolute inset-y-0 inset-s-0 bg-muted-foreground/35 transition-[inline-size] duration-700 motion-reduce:transition-none"
            style:inline-size="{(1 - shareIn) * 100}%"
          ></span>
        </dd>
        <dd class="font-figures font-semibold tabular-nums sm:text-end">
          {percent(1 - shareIn, 0)}
        </dd>
      </dl>
    </div>
  {/if}
</section>
