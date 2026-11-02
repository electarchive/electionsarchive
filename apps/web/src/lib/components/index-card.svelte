<script lang="ts">
  import { resolve } from "$app/paths";
  import type { explain } from "#lib/explain.svelte.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import { about, caveatLabel, scale } from "#lib/indices.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    value,
  }: { value: Extract<NonNullable<typeof explain.value>, { kind: "index" }> } =
    $props();

  const info = $derived(about(value.figure));
  const ruler = $derived(scale(value.figure));
  const isPair = $derived(value.values.length > 1);
  const parts = $derived(
    value.values.map((part, index) => ({
      ...part,
      index,
      band:
        part.value == null
          ? null
          : (ruler.bands[ruler.band(part.value)] ?? null),
      at: part.value == null ? null : ruler.at(part.value),
    })),
  );
  const notes = $derived([
    ...value.notes,
    ...value.caveats.map((caveat) => caveatLabel(caveat)),
  ]);

  const tint = (tone: number) =>
    `color-mix(in oklab, var(--turnout-high) ${Math.round(16 + tone * 84)}%, var(--turnout-low))`;
</script>

{#snippet marker(index: number)}
  <span
    class={cn(
      "inline-block shrink-0 bg-foreground ring-2 ring-popover block-2.5 inline-2.5",
      { "rounded-full": index === 0 },
    )}
    aria-hidden="true"
  ></span>
{/snippet}

<div class="grid gap-4 px-4 pbs-2 pbe-6 max-inline-[62ch]">
  <div class="grid gap-0.5 pe-10">
    <Drawer.Title class="text-base/snug font-semibold text-pretty"
      >{value.title}</Drawer.Title
    >
    <p class="text-sm text-muted-foreground">{value.context}</p>
  </div>
  <div class="flex flex-wrap gap-x-7 gap-y-3">
    {#each parts as part (part.index)}
      <div class="grid content-start gap-1.5">
        <span class="flex items-baseline gap-1.5">
          {#if isPair}
            <span class="self-center">{@render marker(part.index)}</span>
          {/if}
          <span
            class="text-[2rem]/none font-semibold tracking-tight tabular-nums"
            >{part.text}</span
          >
          {#if part.unit != null}
            <span class="text-sm text-muted-foreground">{part.unit}</span>
          {/if}
        </span>
        {#if part.band != null}
          <span class="flex items-center gap-1.5 text-sm font-medium">
            <span
              class="shrink-0 rounded-xs block-2.5 inline-2.5"
              style:background-color={tint(part.band.tone)}
              aria-hidden="true"
            ></span>
            {part.band.name}
          </span>
        {/if}
      </div>
    {/each}
  </div>
  {#if parts.some((part) => part.at != null)}
    <div class="grid gap-1" aria-hidden="true">
      <div class="relative block-6">
        <div class="absolute inset-x-0 inset-be-0 flex gap-0.5 block-2">
          {#each ruler.bands as band (band.name)}
            <span
              class="rounded-xs first:rounded-s-full last:rounded-e-full"
              style:flex="{band.end - band.start} 1 0"
              style:background-color={tint(band.tone)}
            ></span>
          {/each}
        </div>
        {#each parts as part (part.index)}
          {#if part.at != null}
            <span
              class="absolute inset-y-0 flex -translate-x-1/2 flex-col items-center rtl:translate-x-1/2"
              style:inset-inline-start="{part.at * 100}%"
            >
              {@render marker(part.index)}
              <span class="grow bg-foreground inline-0.5"></span>
            </span>
          {/if}
        {/each}
      </div>
      <div
        class="relative text-[0.6875rem]/none text-muted-foreground tabular-nums block-3"
      >
        {#each ruler.ticks as tick (tick.text)}
          <span
            class="absolute -translate-x-1/2 rtl:translate-x-1/2"
            style:inset-inline-start="{tick.at * 100}%">{tick.text}</span
          >
        {/each}
      </div>
      <div class="flex justify-between gap-4 text-xs text-muted-foreground">
        <span>{ruler.low}</span>
        <span class="text-end">{ruler.high}</span>
      </div>
    </div>
  {/if}
  {#if value.reading != null}
    <p class="text-[0.9375rem]/relaxed font-medium text-pretty">
      {value.reading}
    </p>
  {/if}
  <Drawer.Description class="text-[0.9375rem]/relaxed text-pretty"
    >{info.meaning()}</Drawer.Description
  >
  <dl
    class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 border-bs border-border pbs-3 text-sm"
  >
    <dt class="text-muted-foreground">{m.indices_range()}</dt>
    <dd class="text-pretty">{info.range()}</dd>
    <dt class="text-muted-foreground">{m.indices_formula()}</dt>
    <dd class="tabular-nums">{info.formula}</dd>
  </dl>
  {#if notes.length > 0}
    <section class="grid gap-1.5 border-bs border-border pbs-3 text-sm">
      <h3 class="font-semibold">{m.indices_this()}</h3>
      <ul class="grid gap-1 text-pretty">
        {#each notes as note (note)}
          <li>{note}</li>
        {/each}
      </ul>
    </section>
  {/if}
  <a
    class="justify-self-start text-sm font-medium underline decoration-current/35 underline-offset-[0.25em] hover:decoration-current"
    href="{resolve('/[slug=project]', { slug: 'indices' })}#{info.section}"
    >{m.indices_method()}</a
  >
</div>
