<script lang="ts">
  import { percent } from "#lib/format.js";
  import { HATCH } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { mix } from "./model.js";

  const {
    kind,
    sides,
    text,
    blank,
    isLater = false,
  }: {
    kind: "ramp" | "counted" | "votes" | "waiting";
    sides: Array<{ name: string; color: string }>;
    text: string;
    blank: string;
    isLater?: boolean;
  } = $props();

  const STEPS = [26, 52, 78, 100];
</script>

<div class="grid gap-2">
  {#if kind === "ramp" && sides.length > 0}
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
                style:background-color={mix(side.color, step)}
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
  {:else if kind === "counted"}
    <div class="grid gap-1">
      <div class="flex gap-px overflow-hidden rounded-xs" aria-hidden="true">
        {#each [10, 30, 50, 70, 88] as step (step)}
          <span
            class="flex-1 block-2.5"
            style:background-color={mix("var(--foreground)", step)}
          ></span>
        {/each}
      </div>
      <div
        class="flex justify-between font-figures text-[0.6875rem]/4 tabular-nums"
      >
        <span>{percent(0, 0)}</span>
        <span>{percent(1, 0)}</span>
      </div>
    </div>
  {:else if kind === "waiting"}
    <p class="flex items-center gap-2 text-[0.6875rem]/4 font-semibold">
      <span
        class="shrink-0 rounded-xs ring-1 ring-border block-3 inline-4"
        style:background-color={blank}
        aria-hidden="true"
      ></span>
      {m.livemap_legend_waiting()}
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
  <p class="text-xs/4 text-pretty text-muted-foreground">{text}</p>
  {#if isLater}
    <p class="flex items-center gap-2 text-xs/4 text-muted-foreground">
      <span
        class="shrink-0 rounded-xs ring-1 ring-border block-3 inline-4"
        style:background-image={HATCH}
        aria-hidden="true"
      ></span>
      {m.night_later_key()}
    </p>
  {/if}
</div>
