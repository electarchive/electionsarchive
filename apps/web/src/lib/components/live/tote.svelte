<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import CheckIcon from "@lucide/svelte/icons/check";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import type { Standing } from "#lib/live/standing.js";
  import Name from "#lib/components/name.svelte";
  import { count, percent } from "#lib/format.js";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Counter from "./counter.svelte";
  import Face from "./face.svelte";

  const {
    options,
    now,
    estimate,
    winners,
    isWaiting,
    isLater = false,
  }: {
    options: Array<LiveOption>;
    now: Standing;
    estimate: LiveEstimate | null;
    winners: Array<string>;
    isWaiting: boolean;
    isLater?: boolean;
  } = $props();

  let isOpen = $state(false);

  const order = $derived<Array<number>>([
    now.pair[0],
    now.pair[1],
    ...now.order.filter(
      (index) => index !== now.pair[0] && index !== now.pair[1],
    ),
  ]);
  const top = $derived(order.slice(0, 2));
  const rest = $derived(order.slice(2));
  const others = $derived.by(() => {
    let total = 0;
    for (const index of rest) {
      total += now.totals[index] ?? 0;
    }
    return total;
  });
  const share = (index: number) =>
    now.total > 0 ? (now.totals[index] ?? 0) / now.total : 0;
  const shareIn = $derived(
    estimate == null || estimate.status === "waiting" ? null : estimate.shareIn,
  );
  const gap = $derived.by(() => {
    const [first, second] = top;
    if (now.total === 0 || first == null || second == null) {
      return null;
    }
    const votes = (now.totals[first] ?? 0) - (now.totals[second] ?? 0);
    if (votes === 0) {
      return null;
    }
    return {
      index: votes > 0 ? first : second,
      votes: Math.abs(votes),
      margin: Math.abs(votes) / now.total,
    };
  });
  const isProjected = $derived(
    estimate?.status === "estimating" || estimate?.status === "settled",
  );
</script>

<section class="grid gap-3">
  <div class="relative">
    <ol class="grid grid-cols-2 items-end gap-4">
      {#each top as index, position (options[index]?.id ?? index)}
        {const option = $derived(options[index])}
        {const color = $derived(tone(option))}
        {const isWinner = $derived(
          option != null && winners.includes(option.id),
        )}
        {const isEnd = $derived(position === 1)}
        <li
          class={cn("grid gap-1 min-inline-0", {
            "border-s-[3px] ps-3": !isEnd,
            "justify-items-end border-e-[3px] pe-3 text-end": isEnd,
          })}
          style:border-color={color}
        >
          <span
            class={cn("flex items-center gap-3 max-inline-full", {
              "flex-row-reverse": isEnd,
            })}
          >
            <Face
              {option}
              class="block-10 inline-10 sm:block-11 sm:inline-11"
            />
            <span
              class={cn("grid gap-1 min-inline-0", {
                "justify-items-end": isEnd,
              })}
            >
              <span
                class={cn("flex items-center gap-2 max-inline-full", {
                  "flex-row-reverse": isEnd,
                })}
              >
                <Name
                  label={option?.label ?? ""}
                  person={option?.person ?? null}
                  hasSwatch={false}
                  class="truncate text-base/tight font-[720] tracking-tight"
                />
                {#if isWinner}
                  <span
                    class="flex shrink-0 items-center gap-1 rounded-full px-1.5 py-px text-[0.6875rem] font-semibold knockout"
                    style:background-color={color}
                    style:--party={color}
                    ><CheckIcon
                      class="block-3 inline-3"
                      aria-hidden="true"
                    />{m.night_winner()}</span
                  >
                {/if}
              </span>
              <span
                class="truncate text-xs text-muted-foreground max-inline-full"
                >{#if option?.party == null}{m.night_no_party()}{:else}<Name
                    label={option.party.name}
                    party={option.party.id}
                    color={option.party.color}
                    hasSwatch={false}
                  />{/if}</span
              >
            </span>
          </span>
          <span
            class={cn(
              "flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 pbs-1",
              { "flex-row-reverse": isEnd },
            )}
          >
            {#if isWaiting}
              <span
                class="font-figures text-[clamp(1.75rem,1.2rem+1.6vw,2.375rem)] leading-none font-[780] tracking-tight text-muted-foreground/50"
                aria-hidden="true">–</span
              >
            {:else}
              <Counter
                value={share(index)}
                format={(value: number) => percent(value)}
                class="text-[clamp(1.75rem,1.2rem+1.6vw,2.375rem)] leading-none font-[780] tracking-tight"
              />
              <Counter
                value={now.totals[index] ?? 0}
                format={(value: number) =>
                  m.night_votes({ count: count(Math.round(value)) })}
                class="text-[0.8125rem] text-muted-foreground"
              />
            {/if}
          </span>
        </li>
      {/each}
    </ol>
    {#if gap != null}
      <p
        class="pointer-events-none absolute inset-x-0 inset-be-0 hidden justify-items-center gap-0.5 text-center lg:grid"
      >
        <span
          class="font-figures text-lg leading-none font-[760] tabular-nums"
          style:color={tone(options[gap.index])}
          >{surname(options[gap.index])} +{lead(gap.margin)}</span
        >
        <span class="font-figures text-xs text-muted-foreground tabular-nums"
          >{m.night_votes({ count: count(gap.votes) })}</span
        >
      </p>
    {/if}
  </div>

  <div
    class="relative flex overflow-hidden rounded-xs bg-muted block-3"
    role="img"
    aria-label={m.night_majority()}
  >
    {#if now.total > 0}
      {#each [top[0], ...rest, top[1]] as index, position (`${index}-${position}`)}
        {#if index != null}
          <span
            class="transition-[inline-size] duration-700 ease-out block-full motion-reduce:transition-none"
            style:inline-size="{share(index) * 100}%"
            style:background-color={tone(options[index])}
            style:opacity={position === 0 || position === rest.length + 1
              ? 1
              : 0.55}
          ></span>
        {/if}
      {/each}
    {/if}
    <span
      class="absolute inset-y-0 inset-s-1/2 -translate-x-1/2 bg-background inline-0.5"
    ></span>
  </div>

  <div
    class="grid items-start gap-x-6 gap-y-2 text-[0.8125rem] sm:grid-cols-[auto_minmax(0,1fr)]"
  >
    {#if rest.length > 0}
      <button
        type="button"
        class="-mx-1 flex items-center gap-2 justify-self-start rounded-xs px-1 text-muted-foreground hover:highlighter"
        aria-expanded={isOpen}
        onclick={() => (isOpen = !isOpen)}
      >
        <span
          >{rest.length === 1
            ? options[rest[0] ?? 0]?.label
            : m.night_others({ count: rest.length })}</span
        >
        {#if now.total > 0}
          <span class="font-figures font-semibold text-foreground tabular-nums"
            >{percent(others / now.total)}</span
          >
        {/if}
        <ChevronDownIcon
          class={cn("transition-transform block-3.5 inline-3.5", {
            "rotate-180": isOpen,
          })}
          aria-hidden="true"
        />
      </button>
    {:else}
      <span></span>
    {/if}
    {#if !isLater}
      <div
        class="grid grid-cols-[auto_minmax(4rem,1fr)_auto] items-center gap-x-3 gap-y-0.5"
      >
        <span class="font-semibold">{m.night_counted()}</span>
        <div
          class="relative overflow-hidden rounded-full bg-muted block-1.5"
          aria-hidden="true"
        >
          {#if shareIn != null && isProjected}
            <span
              class="absolute inset-y-0 inset-s-0 bg-foreground/25 transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
              style:inline-size="{Math.min(1, shareIn.high) * 100}%"
            ></span>
            <span
              class="absolute inset-y-0 inset-s-0 rounded-full bg-foreground transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
              style:inline-size="{Math.min(1, shareIn.mid) * 100}%"
            ></span>
          {:else if !isWaiting}
            <span
              class="absolute inset-0 bg-[repeating-linear-gradient(-45deg,var(--border)_0_4px,transparent_4px_8px)]"
            ></span>
          {/if}
        </div>
        <span class="font-figures text-muted-foreground tabular-nums">
          {#if isWaiting}
            {m.night_counted_none()}
          {:else if shareIn != null && isProjected}
            {m.night_counted_expected({
              share: percent(Math.min(0.99, shareIn.mid), 0),
            })}
          {:else}
            {m.night_votes({ count: count(now.total) })}
          {/if}
        </span>
        {#if now.reporting != null && now.expected != null && now.expected > 0}
          <span class="col-start-3 text-end text-xs text-muted-foreground">
            {m.night_precincts({
              reporting: count(now.reporting),
              expected: count(now.expected),
            })}
          </span>
        {/if}
      </div>
    {/if}
  </div>

  {#if isOpen && rest.length > 0}
    <ul
      class="grid gap-x-8 gap-y-1 border-bs border-border pbs-2 text-[0.8125rem] sm:grid-cols-2"
    >
      {#each rest as index (options[index]?.id ?? index)}
        {const option = $derived(options[index])}
        <li class="flex items-baseline gap-2">
          <span
            class="self-center rounded-full block-2 inline-2"
            style:background-color={tone(option)}
          ></span>
          <span class="grow truncate"
            ><Name
              label={option?.label ?? ""}
              person={option?.person ?? null}
              hasSwatch={false}
            /><span class="text-muted-foreground">
              · {#if option?.party == null}{m.night_no_party()}{:else}<Name
                  label={option.party.short}
                  party={option.party.id}
                  color={option.party.color}
                  hasSwatch={false}
                />{/if}</span
            ></span
          >
          <span class="font-figures text-muted-foreground tabular-nums"
            >{count(now.totals[index] ?? 0)}</span
          >
          <span class="text-end font-figures tabular-nums min-inline-12"
            >{now.total > 0 ? percent(share(index)) : "–"}</span
          >
        </li>
      {/each}
    </ul>
  {/if}
</section>
