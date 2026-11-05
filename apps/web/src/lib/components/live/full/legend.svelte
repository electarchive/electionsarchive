<script lang="ts">
  import type { Safety } from "#lib/shade.js";
  import { DOTS } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { marginShade, safety } from "#lib/shade.js";
  import Scale from "./scale.svelte";

  const {
    kind,
    sides,
    bins = null,
    text = null,
    waiting = null,
    blank,
    reach = 0.1,
    isLater = false,
  }: {
    kind: "ramp" | "arrows" | "counted" | "votes" | "waiting";
    sides: Array<{ name: string; color: string }>;
    bins?: Array<{ from: number; to: number | null; safety: Safety }> | null;
    text?: string | null;
    waiting?: string | null;
    blank: string;
    reach?: number;
    isLater?: boolean;
  } = $props();

  const STEPS = [0.26, 0.52, 0.78, 1].map((step) => safety(step));
</script>

<div class="grid gap-2">
  {#if kind === "ramp" && bins != null && sides[0] != null && sides[1] != null}
    <Scale sides={[sides[0], sides[1]]} {bins} />
  {:else if kind === "ramp" && sides.length > 0}
    <div class="grid gap-1">
      <div class="flex gap-px overflow-hidden rounded-xs" aria-hidden="true">
        {#each sides as side, index (side.name)}
          <span
            class="flex flex-1 gap-px"
            class:flex-row-reverse={index === 0 && sides.length > 1}
          >
            {#each STEPS as step (step)}
              <span
                class="flex-1 block-2.5"
                style:background-color={marginShade(side.color, step)}
              ></span>
            {/each}
          </span>
        {/each}
      </div>
      <div class="flex justify-between gap-3 text-[0.6875rem]/4 font-semibold">
        {#each sides as side (side.name)}
          <span class="truncate">{side.name}</span>
        {/each}
      </div>
    </div>
  {:else if kind === "arrows" && sides[0] != null && sides[1] != null}
    <div
      class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[0.6875rem]/4"
    >
      <span class="flex items-center gap-1.5 font-semibold">
        {#each [sides[0], sides[1]] as side, at (at)}
          {#if at === 1}
            <svg
              class="block-4 inline-6"
              viewBox="0 0 24 16"
              fill="none"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              style:color={side.color}
              aria-hidden="true"
              ><path
                stroke="currentColor"
                d="M4.6 13.2 19.4 2.8M13.4 3.2l6-.4-2.4 5.5"
              /></svg
            >
          {/if}
          <span class="truncate">{side.name}</span>
          {#if at === 0}
            <svg
              class="-scale-x-100 block-4 inline-6"
              viewBox="0 0 24 16"
              fill="none"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              style:color={side.color}
              aria-hidden="true"
              ><path
                stroke="currentColor"
                d="M4.6 13.2 19.4 2.8M13.4 3.2l6-.4-2.4 5.5"
              /></svg
            >
          {/if}
        {/each}
      </span>
      <span class="font-figures text-muted-foreground tabular-nums"
        >{m.map_swing_scale({
          low: "0",
          high: String(Math.round(reach * 1000) / 10),
        })}</span
      >
    </div>
  {:else if kind === "counted"}
    <Scale />
  {:else if kind === "waiting"}
    <p class="flex items-center gap-2 text-[0.6875rem]/4 font-semibold">
      <span
        class="shrink-0 rounded-xs ring-1 ring-border block-3 inline-4"
        style:background-color={blank}
        aria-hidden="true"
      ></span>
      {waiting ?? m.livemap_legend_waiting()}
    </p>
  {:else if kind === "votes"}
    <div class="flex items-end gap-2" aria-hidden="true">
      {#each [6, 11, 18] as size (size)}
        <span
          class="rounded-full bg-muted-foreground/60 ring-1 ring-card"
          style:block-size="{size}px"
          style:inline-size="{size}px"
        ></span>
      {/each}
    </div>
  {/if}
  {#if text != null}
    <p class="text-xs/4 text-pretty text-muted-foreground">{text}</p>
  {/if}
  {#if isLater}
    <p class="flex items-center gap-2 text-xs/4 text-muted-foreground">
      <span
        class="shrink-0 rounded-xs ring-1 ring-border block-3 inline-4"
        style:background-image={DOTS}
        aria-hidden="true"
      ></span>
      {m.night_later_key()}
    </p>
  {/if}
</div>
