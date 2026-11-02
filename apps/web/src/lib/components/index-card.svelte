<script lang="ts">
  import { resolve } from "$app/paths";
  import type { explain } from "#lib/explain.svelte.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import { about, caveatLabel } from "#lib/indices.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    value,
  }: { value: Extract<NonNullable<typeof explain.value>, { kind: "index" }> } =
    $props();

  const info = $derived(about(value.figure));
  const notes = $derived([
    ...value.notes,
    ...value.caveats.map((caveat) => caveatLabel(caveat)),
  ]);
</script>

<div class="grid gap-4 px-4 pbs-2 pbe-6 max-inline-[62ch]">
  <div class="grid gap-0.5 pe-10">
    <Drawer.Title class="text-base/snug font-semibold text-pretty"
      >{value.title}</Drawer.Title
    >
    <p class="text-sm text-muted-foreground">{value.context}</p>
  </div>
  <p class="flex flex-wrap items-baseline gap-x-5 gap-y-1">
    {#each value.values as part (part.unit)}
      <span class="text-[2rem]/none font-semibold tracking-tight tabular-nums"
        >{part.text}{#if part.unit != null}{" "}<span
            class="text-sm font-normal tracking-normal text-muted-foreground"
            >{part.unit}</span
          >{/if}</span
      >
    {/each}
  </p>
  <Drawer.Description class="text-[0.9375rem]/relaxed text-pretty"
    >{info.meaning()}</Drawer.Description
  >
  <dl
    class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 border-bs border-border pbs-3 text-sm"
  >
    <dt class="text-muted-foreground">{m.indices_formula()}</dt>
    <dd class="tabular-nums">{info.formula}</dd>
    <dt class="text-muted-foreground">{m.indices_range()}</dt>
    <dd class="text-pretty">{info.range()}</dd>
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
