<script lang="ts">
  import { resolve } from "$app/paths";
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import Name from "#lib/components/name.svelte";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const { lanes }: { lanes: PageData["parties"] } = $props();

  const HOW: Record<string, () => string> = {
    nominated: m.person_parties_nominated,
    line: m.person_parties_line,
    endorsed: m.person_parties_endorsed,
    supported: m.person_parties_supported,
    member: m.person_parties_member_ran,
    independent: m.person_parties_independent,
  };
  const today = new Date().toISOString().slice(0, 10);
  const tether = TooltipPrimitive.createTether<number>();

  const isShown = $derived(
    lanes.length > 1 ||
      lanes.some(
        (lane) =>
          lane.party == null ||
          lane.ballots.some(
            (ballot) => ballot.how !== "nominated" && ballot.how !== "line",
          ),
      ),
  );
  const dates = $derived(
    lanes.flatMap((lane) => [
      ...lane.spans.flatMap((span) => [
        ...(span.from == null ? [] : [span.from]),
        span.to ?? today,
      ]),
      ...lane.ballots.map((ballot) => ballot.date),
    ]),
  );
  const low = $derived(
    Date.UTC(
      Number(
        dates.toSorted((a, b) => a.localeCompare(b))[0]?.slice(0, 4) ?? 2000,
      ),
      0,
      1,
    ),
  );
  const high = $derived(
    Date.UTC(
      Number(
        dates
          .toSorted((a, b) => a.localeCompare(b))
          .at(-1)
          ?.slice(0, 4) ?? 2000,
      ) + 1,
      0,
      1,
    ),
  );
  const at = (value: string | null) =>
    (Date.parse(value ?? today) - low) / Math.max(high - low, 1);
  const ticks = $derived.by(() => {
    const first = new Date(low).getUTCFullYear();
    const last = new Date(high).getUTCFullYear();
    const span = last - first;
    const step =
      span <= 12 ? 2 : span <= 30 ? 5 : span <= 80 ? 10 : span <= 160 ? 20 : 50;
    const years: Array<number> = [];
    for (
      let year = Math.ceil(first / step) * step;
      year <= last;
      year += step
    ) {
      years.push(year);
    }
    return years;
  });
  const kinds = $derived(
    new Set(lanes.flatMap((lane) => lane.spans.map((span) => span.kind))),
  );
  const hows = $derived(
    new Set<string>(
      lanes.flatMap((lane) => lane.ballots.map((ballot) => ballot.how)),
    ),
  );
  const ballots = $derived(lanes.flatMap((lane) => lane.ballots));
  const offsets = $derived.by(() => {
    const starts: Array<number> = [];
    let total = 0;
    for (const lane of lanes) {
      starts.push(total);
      total += lane.ballots.length;
    }
    return starts;
  });
  const named = $derived(lanes.filter((lane) => lane.party != null).length);
</script>

{#snippet mark(how: string, color: string)}
  <span
    class={cn(
      "block rounded-full border-2 border-(--party) block-2.5 inline-2.5",
      {
        "bg-(--party)":
          how === "nominated" || how === "line" || how === "member",
        "bg-card": how === "endorsed" || how === "supported",
        "border-dashed bg-card": how === "independent",
      },
      { "ring-2 ring-card ring-offset-0": how === "member" },
    )}
    style:--party={color}
  ></span>
{/snippet}

{#if isShown}
  <section class="col-span-12 grid gap-2.5 bg-card px-3.5 pbs-3 pbe-3.5">
    <h2 class="text-[0.8125rem] font-bold">{m.person_parties()}</h2>
    {#if named > 1}
      <p class="text-sm text-muted-foreground">
        {m.person_parties_lede({
          count: String(named),
          first: String(new Date(low).getUTCFullYear()),
          last: String(new Date(high).getUTCFullYear() - 1),
        })}
      </p>
    {/if}
    <div
      class="grid grid-cols-1 gap-x-4 sm:grid-cols-[minmax(0,13rem)_minmax(0,1fr)]"
    >
      <div class="max-sm:hidden"></div>
      <div class="relative block-5" aria-hidden="true">
        {#each ticks as year (year)}
          <span
            class="absolute inset-bs-0 -translate-x-1/2 font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
            style:inset-inline-start="{at(`${year}-01-01`) * 100}%">{year}</span
          >
        {/each}
      </div>
      {#each lanes as lane, row (lane.party?.id ?? "")}
        {const color = $derived(
          lane.party == null
            ? "var(--muted-foreground)"
            : swatch(lane.party.color, `party:${lane.party.id}`),
        )}
        <div
          class="flex items-center border-bs border-border/60 text-sm min-inline-0 sm:block-9 max-sm:pbs-1.5"
        >
          {#if lane.party == null}
            <span class="truncate font-semibold">{m.person_parties_none()}</span
            >
          {:else}
            <Name
              class="truncate font-semibold"
              label={lane.party.label}
              {color}
              party={lane.party.id}
            />
          {/if}
        </div>
        <div
          class="relative border-border/60 block-9 sm:border-bs"
          style:--party={color}
        >
          {#each ticks as year (year)}
            <span
              class="absolute inset-y-0 border-s border-border/50"
              style:inset-inline-start="{at(`${year}-01-01`) * 100}%"
              aria-hidden="true"
            ></span>
          {/each}
          {#each lane.spans as span, index (index)}
            {const x0 = $derived(at(span.from))}
            {const x1 = $derived(at(span.to))}
            <span
              class={cn("absolute rounded-full", {
                "inset-bs-[calc(50%-0.3125rem)] bg-(--party) block-2.5":
                  span.kind === "office",
                "inset-be-1 bg-(--party)/55 block-1": span.kind === "member",
                "inset-bs-[calc(50%-0.3125rem)] border border-dashed border-(--party) block-2.5":
                  span.kind === "group",
              })}
              style:inset-inline-start="{x0 * 100}%"
              style:inline-size="max(0.25rem, {Math.max(x1 - x0, 0) * 100}%)"
              aria-hidden="true"
            ></span>
          {/each}
          {#each lane.ballots as ballot, index (index)}
            <Tooltip.Trigger
              {tether}
              payload={(offsets[row] ?? 0) + index}
              tabindex={-1}
            >
              {#snippet child({ props })}
                <svelte:element
                  this={ballot.path == null ? "span" : "a"}
                  {...props}
                  type={undefined}
                  class="absolute inset-bs-1/2 z-1 -translate-1/2 rounded-full p-1"
                  style:inset-inline-start="{at(ballot.date) * 100}%"
                  href={ballot.path == null
                    ? undefined
                    : resolve("/[...path]", { path: ballot.path })}
                  aria-label="{ballot.date.slice(0, 4)} {ballot.name}"
                >
                  {@render mark(ballot.how, color)}
                </svelte:element>
              {/snippet}
            </Tooltip.Trigger>
          {/each}
        </div>
      {/each}
    </div>
    <ul
      class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
      style:--party="var(--foreground)"
    >
      {#if kinds.has("office")}
        <li class="flex items-center gap-1.5">
          <span class="rounded-full bg-(--party) block-2.5 inline-5"></span>
          {m.person_parties_office()}
        </li>
      {/if}
      {#if kinds.has("member")}
        <li class="flex items-center gap-1.5">
          <span class="rounded-full bg-(--party)/55 block-1 inline-5"></span>
          {m.person_parties_member()}
        </li>
      {/if}
      {#if kinds.has("group")}
        <li class="flex items-center gap-1.5">
          <span
            class="rounded-full border border-dashed border-(--party) block-2.5 inline-5"
          ></span>
          {m.person_parties_group()}
        </li>
      {/if}
      {#each Object.keys(HOW).filter((how) => hows.has(how)) as how (how)}
        <li class="flex items-center gap-1.5">
          {@render mark(how, "var(--foreground)")}
          {HOW[how]?.()}
        </li>
      {/each}
    </ul>
  </section>

  <Tooltip.Root {tether} delayDuration={120}>
    {#snippet children({ payload })}
      {const ballot = $derived(
        payload == null ? undefined : ballots.at(Number(payload)),
      )}
      {#if ballot != null}
        <Tooltip.Content
          collisionPadding={8}
          class="grid gap-0.5 leading-snug inline-[min(18rem,calc(100vw-2rem))]"
        >
          <span class="font-semibold">{ballot.name}</span>
          <span class="text-muted-foreground"
            >{date(ballot.date)} · {HOW[ballot.how]?.() ?? ballot.how}</span
          >
        </Tooltip.Content>
      {/if}
    {/snippet}
  </Tooltip.Root>
{/if}
