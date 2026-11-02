<script lang="ts">
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import type { Lens } from "#lib/atlas/color.js";
  import type { Sheet } from "#lib/atlas/data.js";
  import type { View } from "#lib/atlas/state.svelte.js";
  import {
    admits,
    band,
    mix,
    read,
    scale,
    strengths,
  } from "#lib/atlas/color.js";
  import Histogram from "#lib/atlas/histogram.svelte";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { Switch } from "#lib/components/ui/switch/index.js";
  import { count, percent } from "#lib/format.js";
  import { largest } from "#lib/map/engine.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    view,
    lens,
    sets,
    legend,
    targets,
    year,
    isProportional,
    isAllied,
    isEstimated,
    noun,
  }: {
    view: View;
    lens: Lens;
    sets: Array<Sheet>;
    legend: Sheet["legend"];
    targets: Sheet["targets"];
    year: string | null;
    isProportional: boolean;
    isAllied: boolean;
    isEstimated: boolean;
    noun: string;
  } = $props();

  const picks = $derived(legend.slice(0, 3));
  const extras = $derived(legend.slice(3));
  const extra = $derived(
    lens.mode === "share"
      ? (extras.find((item) => item.key === lens.whom) ?? null)
      : null,
  );
  const more = $derived(
    extras.every((item) => item.key.startsWith("person:"))
      ? m.map_more_candidates()
      : m.map_more_parties(),
  );
  const name = (item: Sheet["legend"][number] | undefined) =>
    item?.short ?? item?.label.replace(/ \([^)]*\)$/u, "") ?? "";
  const first = $derived(legend.find((item) => item.key === lens.first));
  const second = $derived(legend.find((item) => item.key === lens.second));
  const span = $derived(scale(lens.mode));

  const stats = $derived.by(() => {
    const [low, high] = span.domain;
    const total = Math.round((high - low) / span.step);
    const tally = Array.from<number>({ length: total }).fill(0);
    const won = new Map<string, number>();
    let admitted = 0;
    let measured = 0;
    let places = 0;
    for (const set of sets) {
      for (const [i] of set.places.k.entries()) {
        const reading = read(set, i, lens);
        if (reading.leader == null) {
          continue;
        }
        places += 1;
        const owner =
          reading.held != null && !lens.isBanded
            ? (largest(
                reading.held,
                (index) => set.places.shares[index]?.[i] ?? 0,
              ) ?? reading.leader)
            : reading.leader;
        const key = set.legend[owner]?.key;
        if (key != null && !(lens.isBanded && reading.held != null)) {
          won.set(key, (won.get(key) ?? 0) + 1);
        }
        if (admits(set, reading, lens)) {
          admitted += 1;
        }
        if (reading.value == null) {
          continue;
        }
        measured += 1;
        const at = Math.min(
          total - 1,
          Math.max(0, Math.floor((reading.value - low) / span.step)),
        );
        tally[at] = (tally[at] ?? 0) + 1;
      }
    }
    return {
      admitted,
      measured,
      places,
      won: [...won]
        .map(([key, value]) => ({
          item: legend.find((entry) => entry.key === key),
          won: value,
        }))
        .filter((entry) => entry.item != null)
        .toSorted((a, b) => b.won - a.won),
      bars: tally.map((tallied, index) => {
        const middle = low + (index + 0.5) * span.step;
        return {
          count: tallied,
          color: band(middle, lens),
          isShown:
            lens.range == null ||
            (middle >= lens.range[0] - span.step / 2 &&
              middle <= lens.range[1] + span.step / 2),
        };
      }),
    };
  });

  const ticks = $derived(
    lens.mode === "share"
      ? [0, 0.5, 1].map((at) => ({
          at,
          label: percent(at).replace(/[.,]0/u, ""),
        }))
      : [
          {
            at: span.domain[0],
            label: m.atlas_lead({
              name: name(second),
              points: String(Math.round(-span.domain[0] * 100)),
            }),
          },
          {
            at: 0,
            label: lens.mode === "swing" ? m.atlas_no_shift() : m.atlas_even(),
          },
          {
            at: span.domain[1],
            label: m.atlas_lead({
              name: name(first),
              points: String(Math.round(span.domain[1] * 100)),
            }),
          },
        ],
  );
  const isFiltered = $derived(
    view.range != null || view.only != null || view.isFlippedOnly,
  );
  const ramp = $derived(strengths(lens));
</script>

<div class="grid grid-cols-[minmax(0,1fr)] gap-4">
  <div class="grid grid-cols-[minmax(0,1fr)] gap-1.5">
    <span class="text-xs font-semibold text-muted-foreground"
      >{m.map_color_by()}</span
    >
    <div class="flex flex-wrap gap-1.5">
      {#each [{ value: "winner" as const, label: m.map_mode_winner() }, ...(isProportional ? [{ value: "split" as const, label: m.map_mode_split() }] : []), { value: "votes" as const, label: m.map_mode_bubbles() }, ...(lens.isCompared ? [{ value: "swing" as const, label: m.map_mode_swing() }, { value: "flips" as const, label: m.map_mode_flips() }] : [])] as choice (choice.value)}
        <button
          type="button"
          class={cn(
            "flex items-center gap-1.5 rounded-sm px-2 text-[0.8125rem] font-semibold whitespace-nowrap ring-1 ring-input block-7 max-inline-full min-inline-0 hover:bg-secondary",
            {
              "bg-foreground text-card ring-foreground hover:bg-foreground":
                lens.mode === choice.value,
            },
          )}
          aria-pressed={lens.mode === choice.value}
          onclick={() => {
            view.mode = choice.value;
            view.range = null;
          }}>{choice.label}</button
        >
      {/each}
      {#each picks as item (item.key)}
        {const isOn = $derived(lens.mode === "share" && lens.whom === item.key)}
        <button
          type="button"
          class={cn(
            "flex items-center gap-1.5 rounded-sm px-2 text-[0.8125rem] font-semibold whitespace-nowrap ring-1 ring-input block-7 max-inline-full min-inline-0 hover:bg-secondary",
            {
              "bg-foreground text-card ring-foreground hover:bg-foreground":
                isOn,
            },
          )}
          aria-pressed={isOn}
          onclick={() => {
            view.mode = "share";
            view.who = item.key;
            view.range = null;
          }}
        >
          <span
            class="shrink-0 rounded-full block-2 inline-2"
            style:background={lens.tone(item.key)}
          ></span>
          <span class="truncate">{name(item)}</span>
        </button>
      {/each}
      {#if extras.length > 0}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger
            class={cn(
              "flex items-center gap-1.5 rounded-sm px-2 text-[0.8125rem] font-semibold whitespace-nowrap ring-1 ring-input block-7 max-inline-full min-inline-0 hover:bg-secondary",
              "pe-1.5",
              {
                "bg-foreground text-card ring-foreground hover:bg-foreground":
                  extra != null,
              },
            )}
            aria-pressed={extra != null}
          >
            {#if extra != null}
              <span
                class="shrink-0 rounded-full block-2 inline-2"
                style:background={lens.tone(extra.key)}
              ></span>
            {/if}
            <span class="truncate">{extra == null ? more : name(extra)}</span>
            <ChevronDownIcon class="shrink-0 block-3.5 inline-3.5" />
          </DropdownMenu.Trigger>
          <DropdownMenu.Content
            align="start"
            class="overflow-y-auto max-block-80 min-inline-56"
          >
            {#each extras as item (item.key)}
              <DropdownMenu.Item
                onSelect={() => {
                  view.mode = "share";
                  view.who = item.key;
                  view.range = null;
                }}
              >
                <span
                  class="shrink-0 rounded-full block-2 inline-2"
                  style:background={lens.tone(item.key)}
                ></span>
                <span class="truncate">{item.label}</span>
              </DropdownMenu.Item>
            {/each}
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      {/if}
    </div>
  </div>

  {#if targets.length > 0}
    <div class="grid grid-cols-[minmax(0,1fr)] gap-1.5">
      <span class="text-xs font-semibold text-muted-foreground"
        >{m.atlas_compare()}</span
      >
      <Select.Root
        type="single"
        value={view.vs ?? ""}
        onValueChange={(value) => {
          view.vs = value === "" ? null : value;
          if (
            value === "" &&
            (view.mode === "swing" || view.mode === "flips")
          ) {
            view.mode = "winner";
          }
          view.range = null;
          view.isFlippedOnly = false;
        }}
      >
        <Select.Trigger class="inline-full">
          {targets.find((target) => target.id === view.vs)?.label ??
            m.atlas_compare_none()}
        </Select.Trigger>
        <Select.Content>
          <Select.Item value="" label={m.atlas_compare_none()} />
          {#each targets as target (target.id)}
            <Select.Item value={target.id} label={target.label} />
          {/each}
        </Select.Content>
      </Select.Root>
      {#if isEstimated && year != null}
        <p class="text-xs text-pretty text-muted-foreground">
          {m.atlas_estimated_note({ year })}
        </p>
      {/if}
    </div>
  {/if}

  {#if isAllied}
    <div class="grid grid-cols-[minmax(0,1fr)] gap-1.5">
      <span class="text-xs font-semibold text-muted-foreground"
        >{m.group_label()}</span
      >
      <Segmented
        label={m.group_label()}
        size="sm"
        fill
        options={[
          { value: "group", label: m.group_option() },
          { value: "alliance", label: m.group_alliance() },
        ]}
        value={view.group === "alliance" ? "alliance" : "group"}
        onValueChange={(value) => {
          view.group = value === "alliance" ? "alliance" : null;
          view.only = null;
        }}
      />
    </div>
  {/if}

  <div class="grid grid-cols-[minmax(0,1fr)] gap-2 border-bs pbs-3">
    {#if lens.mode === "winner" || lens.mode === "split" || lens.mode === "flips"}
      <div class="flex items-baseline justify-between gap-2">
        <span class="text-xs font-semibold text-muted-foreground"
          >{lens.mode === "flips" && year != null
            ? m.atlas_flipped({ year })
            : isProportional
              ? m.atlas_won()
              : m.atlas_led()}</span
        >
      </div>
      <ul class="grid gap-0.5">
        {#each stats.won as entry (entry.item?.key)}
          {const key = $derived(entry.item?.key ?? "")}
          {const isOn = $derived(view.only === key)}
          <li>
            <button
              type="button"
              class={cn(
                "flex items-center gap-2 rounded-xs px-1.5 text-start text-[0.8125rem] block-7 inline-full hover:bg-secondary",
                {
                  "bg-secondary font-semibold": isOn,
                  "opacity-55": view.only != null && !isOn,
                },
              )}
              aria-pressed={isOn}
              title={m.atlas_only({ name: name(entry.item) })}
              onclick={() => (view.only = isOn ? null : key)}
            >
              <span
                class="shrink-0 rounded-xs block-3 inline-3"
                style:background={lens.tone(key)}
              ></span>
              <span class="flex-1 truncate">{entry.item?.label}</span>
              <span class="font-figures text-muted-foreground tabular-nums"
                >{count(entry.won)}</span
              >
            </button>
          </li>
        {/each}
      </ul>
    {/if}

    {#if lens.mode !== "votes" && lens.mode !== "flips"}
      <div class="flex items-stretch gap-px" aria-hidden="true">
        {#each ramp as stop (stop.strength)}
          <span
            class="flex-1 block-2"
            style:background={mix(
              lens.mode === "share"
                ? lens.tone(lens.whom)
                : "var(--muted-foreground)",
              stop.strength,
            )}
          ></span>
        {/each}
      </div>
      <Histogram
        bars={stats.bars}
        domain={span.domain}
        step={span.step}
        marker={null}
        {ticks}
        label={m.atlas_range()}
        bind:range={
          () => view.range,
          (value) => {
            view.range = value ?? null;
          }
        }
      />
    {/if}

    {#if lens.isCompared}
      <label class="flex items-center justify-between gap-2 text-[0.8125rem]">
        {m.atlas_flipped_only()}
        <Switch
          checked={view.isFlippedOnly}
          onCheckedChange={(value: boolean) => (view.isFlippedOnly = value)}
        />
      </label>
    {/if}

    {#if isFiltered}
      <div class="flex items-center justify-between gap-2 text-[0.8125rem]">
        <span class="font-figures text-muted-foreground tabular-nums"
          >{m.atlas_shown({
            shown: count(stats.admitted),
            total: count(stats.places),
          })}</span
        >
        <button
          type="button"
          class="font-semibold underline decoration-border underline-offset-3 hover:decoration-foreground"
          onclick={() => {
            view.range = null;
            view.only = null;
            view.isFlippedOnly = false;
          }}>{m.atlas_clear()}</button
        >
      </div>
    {/if}

    {#if lens.mode === "split" || (lens.mode === "winner" && isProportional)}
      <p class="flex items-center gap-2 text-xs text-muted-foreground">
        <span
          class="shrink-0 rounded-xs ring-1 ring-border block-3 inline-3"
          style:background="repeating-linear-gradient(45deg, {lens.tone(
            legend[0]?.key,
          )} 0 3px, {lens.tone(legend[1]?.key)} 3px 6px)"
        ></span>
        {lens.isBanded ? m.map_split_key() : m.map_largest_key({ noun })}
      </p>
    {/if}
  </div>
</div>
