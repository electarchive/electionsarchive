<script lang="ts">
  import ChevronFirstIcon from "@lucide/svelte/icons/chevron-first";
  import ChevronLastIcon from "@lucide/svelte/icons/chevron-last";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import StepBackIcon from "@lucide/svelte/icons/step-back";
  import StepForwardIcon from "@lucide/svelte/icons/step-forward";
  import XIcon from "@lucide/svelte/icons/x";
  import { onMount } from "svelte";
  import { on } from "svelte/events";
  import type {
    Edge,
    EdgeKind,
    Order,
    Plan,
    Scenario,
    Seed,
  } from "#lib/live/simulate.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { count, percent } from "#lib/format.js";
  import {
    clock as clockOf,
    day,
    lead,
    stamp,
    surname,
  } from "#lib/live/format.js";
  import { MINUTE, solveSwing, start } from "#lib/live/simulate.js";
  import { sum } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  let {
    seed,
    planned,
    marks,
    scenario = $bindable(),
    edges = $bindable(),
    clock = $bindable(),
    isOffline = $bindable(),
    at = $bindable(),
  }: {
    seed: Seed;
    planned: Plan;
    marks: Array<number>;
    scenario: Scenario;
    edges: Array<Edge>;
    clock: number;
    isOffline: boolean;
    at: string | null;
  } = $props();

  type Speed = 1 | 10 | 60 | 300 | "updates";
  type Panel = "scenario" | "edges" | "truth";

  const SPEEDS: Array<{ id: Speed; label: () => string }> = [
    { id: 1, label: () => m.night_tool_speed({ times: "1" }) },
    { id: 10, label: () => m.night_tool_speed({ times: "10" }) },
    { id: 60, label: () => m.night_tool_speed({ times: "60" }) },
    { id: 300, label: () => m.night_tool_speed({ times: "300" }) },
    { id: "updates", label: m.night_tool_updates },
  ];
  const ORDERS: Array<{ id: Order; label: () => string }> = [
    { id: "realistic", label: m.night_tool_order_realistic },
    { id: "urban-first", label: m.night_tool_order_urban },
    { id: "rural-first", label: m.night_tool_order_rural },
    { id: "mail-last", label: m.night_tool_order_mail },
    { id: "shuffled", label: m.night_tool_order_shuffled },
  ];
  const EDGES: Array<{
    id: EdgeKind;
    label: () => string;
    hint: () => string;
  }> = [
    {
      id: "correction",
      label: m.night_tool_edge_correction,
      hint: m.night_tool_edge_correction_hint,
    },
    {
      id: "stall",
      label: m.night_tool_edge_stall,
      hint: m.night_tool_edge_stall_hint,
    },
    {
      id: "withdrawn",
      label: m.night_tool_edge_withdrawn,
      hint: m.night_tool_edge_withdrawn_hint,
    },
    {
      id: "overcount",
      label: m.night_tool_edge_overcount,
      hint: m.night_tool_edge_overcount_hint,
    },
    {
      id: "empty",
      label: m.night_tool_edge_empty,
      hint: m.night_tool_edge_empty_hint,
    },
    {
      id: "late",
      label: m.night_tool_edge_late,
      hint: m.night_tool_edge_late_hint,
    },
    {
      id: "certified",
      label: m.night_tool_edge_certified,
      hint: m.night_tool_edge_certified_hint,
    },
  ];

  let isPlaying = $state(false);
  let speed = $state<Speed>(60);
  let panel = $state<Panel | null>(null);

  const first = $derived(planned.start);
  const last = $derived(planned.end + 2 * 60 * MINUTE);
  const a = $derived(seed.race.options[planned.pair[0]]);
  const b = $derived(seed.race.options[planned.pair[1]]);
  const midnight = $derived(
    Date.parse(`${seed.election.date}T00:00:00Z`) + 29 * 60 * MINUTE,
  );
  const truthTotals = $derived(
    seed.race.options.map((_, option) =>
      sum(planned.truth.map((row) => row[option] ?? 0)),
    ),
  );
  const truthAll = $derived(sum(truthTotals));
  const nextMark = $derived(marks.find((mark) => mark > clock) ?? null);
  const previousMark = $derived(marks.findLast((mark) => mark < clock) ?? null);
  const counted = $derived(marks.filter((mark) => mark <= clock).length);

  function go(value: number) {
    clock = Math.max(first, Math.min(last, Math.round(value)));
    at = null;
  }

  function toggle() {
    if (!isPlaying && clock >= last) {
      go(first);
    }
    isPlaying = !isPlaying;
  }

  function step(direction: 1 | -1) {
    isPlaying = false;
    const target = direction === 1 ? nextMark : previousMark;
    if (target != null) {
      go(target);
    }
  }

  function inject(kind: EdgeKind) {
    edges = [
      ...edges.filter((edge) => edge.kind !== kind || kind !== "certified"),
      { kind, at: clock },
    ];
  }

  function preset(id: "baseline" | "tie" | "a" | "b" | "mail") {
    const base = { ...start(seed), seed: scenario.seed };
    switch (id) {
      case "baseline": {
        scenario = base;
        break;
      }
      case "tie": {
        scenario = { ...base, swing: solveSwing(seed, base, 0.0006) };
        break;
      }
      case "a": {
        scenario = { ...base, swing: solveSwing(seed, base, 0.09) };
        break;
      }
      case "b": {
        scenario = { ...base, swing: solveSwing(seed, base, -0.09) };
        break;
      }
      case "mail": {
        scenario = {
          ...base,
          order: "mail-last",
          earlyLean: Math.sign(base.earlyLean) * 26,
          earlyShare: 0.6,
        };
        break;
      }
    }
  }

  $effect(() => {
    if (!isPlaying) {
      return;
    }
    if (speed === "updates") {
      const timer = setInterval(() => {
        const target = marks.find((mark) => mark > clock);
        if (target == null) {
          isPlaying = false;
          return;
        }
        go(target);
      }, 1200);
      return () => clearInterval(timer);
    }
    const rate = speed;
    let previous = performance.now();
    let frame = requestAnimationFrame(function run(time) {
      const next = clock + (time - previous) * rate;
      previous = time;
      if (next >= last) {
        go(last);
        isPlaying = false;
        return;
      }
      clock = next;
      at = null;
      frame = requestAnimationFrame(run);
    });
    return () => cancelAnimationFrame(frame);
  });

  onMount(() => {
    const handle = (event: KeyboardEvent) => {
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey
      ) {
        return;
      }
      if (event.key === " ") {
        event.preventDefault();
        toggle();
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        if (event.shiftKey) {
          go(clock + 10 * MINUTE);
        } else {
          step(1);
        }
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        if (event.shiftKey) {
          go(clock - 10 * MINUTE);
        } else {
          step(-1);
        }
      } else if (/^[1-5]$/u.test(event.key)) {
        speed = SPEEDS[Number(event.key) - 1]?.id ?? speed;
      }
    };
    return on(window, "keydown", handle);
  });

  const x = (time: number) =>
    ((time - first) / Math.max(1, last - first)) * 100;
  const signed = (value: number) =>
    `${surname(value >= 0 ? a : b)} +${lead(value)}`;
</script>

<div
  class="pointer-events-none fixed inset-x-0 inset-be-3 z-40 flex justify-center px-3 sm:inset-be-5"
>
  <div
    class="pointer-events-auto grid gap-px overflow-hidden rounded-lg border border-foreground/15 bg-border shadow-menu inline-full max-inline-240"
    role="region"
    aria-label={m.night_tool_label()}
  >
    {#if panel === "scenario"}
      <section
        class="grid gap-4 overflow-y-auto bg-background p-4 max-block-[50vh] sm:grid-cols-2"
      >
        <div class="grid gap-2 sm:col-span-2">
          <span class="text-xs font-semibold">{m.night_tool_presets()}</span>
          <div class="flex flex-wrap gap-1.5">
            <button
              type="button"
              class="rounded-sm border border-border px-2.5 py-1 text-xs font-semibold hover:highlighter"
              onclick={() => preset("baseline")}
              >{m.night_tool_preset_baseline()}</button
            >
            <button
              type="button"
              class="rounded-sm border border-border px-2.5 py-1 text-xs font-semibold hover:highlighter"
              onclick={() => preset("tie")}>{m.night_tool_preset_tie()}</button
            >
            <button
              type="button"
              class="rounded-sm border border-border px-2.5 py-1 text-xs font-semibold hover:highlighter"
              onclick={() => preset("a")}
              >{m.night_tool_preset_win({ name: surname(a) })}</button
            >
            <button
              type="button"
              class="rounded-sm border border-border px-2.5 py-1 text-xs font-semibold hover:highlighter"
              onclick={() => preset("b")}
              >{m.night_tool_preset_win({ name: surname(b) })}</button
            >
            <button
              type="button"
              class="rounded-sm border border-border px-2.5 py-1 text-xs font-semibold hover:highlighter"
              onclick={() => preset("mail")}
              >{m.night_tool_preset_mail()}</button
            >
          </div>
        </div>
        <label class="grid gap-1 text-xs">
          <span class="flex justify-between font-semibold"
            >{m.night_tool_swing()}<span class="font-figures tabular-nums"
              >{signed(scenario.swing / 100)}</span
            ></span
          >
          <input
            type="range"
            min="-12"
            max="12"
            step="0.1"
            bind:value={scenario.swing}
            class="accent-foreground"
          />
        </label>
        <label class="grid gap-1 text-xs">
          <span class="flex justify-between font-semibold"
            >{m.night_tool_turnout()}<span class="font-figures tabular-nums"
              >{percent(scenario.turnout, 0)}</span
            ></span
          >
          <input
            type="range"
            min="0.5"
            max="1.15"
            step="0.01"
            bind:value={scenario.turnout}
            class="accent-foreground"
          />
        </label>
        <label class="grid gap-1 text-xs">
          <span class="flex justify-between font-semibold"
            >{m.night_tool_lean()}<span class="font-figures tabular-nums"
              >{signed(scenario.earlyLean / 100)}</span
            ></span
          >
          <input
            type="range"
            min="-30"
            max="30"
            step="1"
            bind:value={scenario.earlyLean}
            class="accent-foreground"
          />
        </label>
        <label class="grid gap-1 text-xs">
          <span class="flex justify-between font-semibold"
            >{m.night_tool_early()}<span class="font-figures tabular-nums"
              >{percent(scenario.earlyShare, 0)}</span
            ></span
          >
          <input
            type="range"
            min="0.2"
            max="0.8"
            step="0.01"
            bind:value={scenario.earlyShare}
            class="accent-foreground"
          />
        </label>
        <label class="grid gap-1 text-xs">
          <span class="flex justify-between font-semibold"
            >{m.night_tool_pace()}<span class="font-figures tabular-nums"
              >{m.night_tool_speed({ times: scenario.pace.toFixed(1) })}</span
            ></span
          >
          <input
            type="range"
            min="0.4"
            max="2.5"
            step="0.1"
            bind:value={scenario.pace}
            class="accent-foreground"
          />
        </label>
        <div class="grid gap-1 text-xs">
          <span class="font-semibold">{m.night_tool_seed()}</span>
          <div class="flex gap-1.5">
            <input
              type="number"
              bind:value={scenario.seed}
              aria-label={m.night_tool_seed()}
              class="grow rounded-sm border border-border bg-background px-2 py-1 font-figures tabular-nums min-inline-0"
            />
            <button
              type="button"
              class="rounded-sm border border-border px-2.5 py-1 font-semibold hover:highlighter"
              onclick={() =>
                (scenario.seed = Math.floor(Math.random() * 1_000_000))}
              >{m.night_tool_reroll()}</button
            >
          </div>
        </div>
        <div class="grid gap-1 text-xs sm:col-span-2">
          <span class="font-semibold">{m.night_tool_order()}</span>
          <Segmented
            options={ORDERS.map((order) => ({
              value: order.id,
              label: order.label(),
            }))}
            bind:value={scenario.order}
            label={m.night_tool_order()}
          />
        </div>
      </section>
    {:else if panel === "edges"}
      <section
        class="grid gap-3 overflow-y-auto bg-background p-4 max-block-[50vh]"
      >
        <p class="text-xs text-muted-foreground">{m.night_tool_edges_help()}</p>
        <div class="grid gap-1.5 sm:grid-cols-2">
          {#each EDGES as edge (edge.id)}
            <button
              type="button"
              class="grid gap-0.5 rounded-sm border border-border px-2.5 py-1.5 text-start hover:highlighter"
              onclick={() => inject(edge.id)}
            >
              <span class="text-xs font-semibold">{edge.label()}</span>
              <span class="text-[0.6875rem] text-muted-foreground"
                >{edge.hint()}</span
              >
            </button>
          {/each}
          <button
            type="button"
            class={cn(
              "grid gap-0.5 rounded-sm border px-2.5 py-1.5 text-start",
              {
                "border-foreground bg-foreground text-background": isOffline,
                "border-border hover:highlighter": !isOffline,
              },
            )}
            aria-pressed={isOffline}
            onclick={() => (isOffline = !isOffline)}
          >
            <span class="text-xs font-semibold"
              >{m.night_tool_edge_offline()}</span
            >
            <span
              class={cn("text-[0.6875rem]", {
                "text-muted-foreground": !isOffline,
              })}>{m.night_tool_edge_offline_hint()}</span
            >
          </button>
          <button
            type="button"
            class="grid gap-0.5 rounded-sm border border-border px-2.5 py-1.5 text-start hover:highlighter"
            onclick={() => go(planned.close - 20 * MINUTE)}
          >
            <span class="text-xs font-semibold"
              >{m.night_tool_edge_before()}</span
            >
            <span class="text-[0.6875rem] text-muted-foreground"
              >{m.night_tool_edge_before_hint()}</span
            >
          </button>
        </div>
        {#if edges.length > 0}
          <div class="grid gap-1">
            <span
              class="flex items-center justify-between text-xs font-semibold"
            >
              {m.night_tool_edges_active()}
              <button
                type="button"
                class="rounded-xs px-1 text-muted-foreground underline underline-offset-2"
                onclick={() => (edges = [])}>{m.night_tool_clear()}</button
              >
            </span>
            <ul class="flex flex-wrap gap-1.5">
              {#each edges as edge, index (`${edge.kind}-${edge.at}-${index}`)}
                <li
                  class="flex items-center gap-1 rounded-full border border-border py-0.5 ps-2.5 pe-1 text-xs"
                >
                  {EDGES.find((entry) => entry.id === edge.kind)?.label()} ·
                  <span class="font-figures tabular-nums"
                    >{clockOf(edge.at)}</span
                  >
                  <button
                    type="button"
                    class="rounded-full p-0.5 hover:highlighter"
                    aria-label={m.night_tool_remove()}
                    onclick={() =>
                      (edges = edges.filter(
                        (_, position) => position !== index,
                      ))}
                    ><XIcon
                      class="block-3 inline-3"
                      aria-hidden="true"
                    /></button
                  >
                </li>
              {/each}
            </ul>
          </div>
        {/if}
      </section>
    {:else if panel === "truth"}
      <section class="grid gap-2 bg-background p-4 text-sm">
        <p class="text-xs text-muted-foreground">{m.night_tool_truth_help()}</p>
        <p class="font-serif text-lg">
          {m.night_tool_truth({
            margin: signed(planned.finalMargin),
            votes: count(Math.round(truthAll)),
          })}
        </p>
        <ul class="grid gap-0.5 text-xs">
          {#each seed.race.options as option, index (option.id)}
            <li class="flex justify-between gap-3">
              <span>{option.label}</span>
              <span class="font-figures tabular-nums"
                >{truthAll > 0
                  ? percent((truthTotals[index] ?? 0) / truthAll)
                  : "–"}</span
              >
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    <div
      class="grid gap-2 bg-background/95 px-3 py-2.5 backdrop-blur-md sm:px-4"
    >
      <div class="relative block-6">
        <div
          class="pointer-events-none absolute inset-x-0 inset-bs-1/2 -translate-y-1/2 block-3"
          aria-hidden="true"
        >
          {#each marks as mark (mark)}
            <span
              class="absolute inset-y-0 bg-foreground/25 inline-px"
              style:left="{x(mark)}%"
            ></span>
          {/each}
          <span
            class="absolute -inset-y-1 bg-foreground inline-0.5"
            style:left="{x(planned.close)}%"
          ></span>
          {#each edges as edge, index (`${edge.kind}-${edge.at}-${index}`)}
            <span
              class="absolute -inset-bs-2 -translate-x-1/2 rounded-full bg-(--no) block-1.5 inline-1.5"
              style:left="{x(edge.at)}%"
            ></span>
          {/each}
        </div>
        <input
          type="range"
          min={first}
          max={last}
          step={MINUTE}
          value={clock}
          oninput={(event) => {
            isPlaying = false;
            go(Number(event.currentTarget.value));
          }}
          aria-label={m.night_tool_scrub()}
          aria-valuetext={stamp(clock)}
          class="relative accent-foreground block-6 inline-full"
        />
      </div>
      <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div class="flex items-center gap-0.5">
          <button
            type="button"
            class="rounded-sm p-1.5 hover:highlighter"
            aria-label={m.night_tool_start()}
            onclick={() => go(first)}
            ><ChevronFirstIcon
              class="block-4 inline-4"
              aria-hidden="true"
            /></button
          >
          <button
            type="button"
            class="rounded-sm p-1.5 hover:highlighter disabled:opacity-40"
            aria-label={m.night_tool_back()}
            disabled={previousMark == null}
            onclick={() => step(-1)}
            ><StepBackIcon
              class="block-4 inline-4"
              aria-hidden="true"
            /></button
          >
          <button
            type="button"
            class="rounded-full bg-foreground p-2 text-background"
            aria-label={isPlaying ? m.night_tool_pause() : m.night_tool_play()}
            aria-pressed={isPlaying}
            onclick={toggle}
          >
            {#if isPlaying}
              <PauseIcon class="block-4 inline-4" aria-hidden="true" />
            {:else}
              <PlayIcon class="block-4 inline-4" aria-hidden="true" />
            {/if}
          </button>
          <button
            type="button"
            class="rounded-sm p-1.5 hover:highlighter disabled:opacity-40"
            aria-label={m.night_tool_forward()}
            disabled={nextMark == null}
            onclick={() => step(1)}
            ><StepForwardIcon
              class="block-4 inline-4"
              aria-hidden="true"
            /></button
          >
          <button
            type="button"
            class="rounded-sm p-1.5 hover:highlighter"
            aria-label={m.night_tool_end()}
            onclick={() => go(planned.end)}
            ><ChevronLastIcon
              class="block-4 inline-4"
              aria-hidden="true"
            /></button
          >
        </div>
        <span class="grid leading-tight">
          <span class="font-figures text-sm font-semibold tabular-nums"
            >{stamp(clock)}</span
          >
          <span class="text-[0.6875rem] text-muted-foreground"
            >{day(clock)} · {m.night_tool_count({
              shown: count(counted),
              total: count(marks.length),
            })}</span
          >
        </span>
        <Segmented
          size="sm"
          options={SPEEDS.map((option) => ({
            value: option.id,
            label: option.label(),
          }))}
          bind:value={speed}
          label={m.night_tool_speed_label()}
        />
        <div class="flex flex-wrap gap-1 text-xs">
          <button
            type="button"
            class="rounded-sm px-2 py-1 font-semibold hover:highlighter"
            onclick={() => go(planned.close)}
            >{m.night_tool_jump_close()}</button
          >
          <button
            type="button"
            class="rounded-sm px-2 py-1 font-semibold hover:highlighter"
            onclick={() => go(midnight)}>{m.night_tool_jump_midnight()}</button
          >
        </div>
        <div class="ms-auto flex gap-1">
          {#each [{ id: "scenario", label: m.night_tool_scenario() }, { id: "edges", label: m.night_tool_edges() }, { id: "truth", label: m.night_tool_reveal() }] as const as entry (entry.id)}
            <button
              type="button"
              class={cn("rounded-sm border px-2.5 py-1 text-xs font-semibold", {
                "border-foreground bg-foreground text-background":
                  panel === entry.id,
                "border-border hover:highlighter": panel !== entry.id,
              })}
              aria-expanded={panel === entry.id}
              onclick={() => (panel = panel === entry.id ? null : entry.id)}
              >{entry.label}</button
            >
          {/each}
        </div>
      </div>
    </div>
  </div>
</div>
