<script lang="ts">
  import "./lab.css";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { mode, setMode, userPrefersMode } from "mode-watcher";
  import { MediaQuery } from "svelte/reactivity";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import Chamber from "./chamber.svelte";
  import { copy } from "./copy.js";
  import Indelible from "./indelible.svelte";
  import Tally from "./tally.svelte";
  import { LabView } from "./view.svelte.js";

  const directions = [
    ["indelible", copy.lab_indelible, copy.lab_indelible_about],
    ["tally", copy.lab_tally, copy.lab_tally_about],
    ["chamber", copy.lab_chamber, copy.lab_chamber_about],
  ] as const;

  type Direction = (typeof directions)[number][0];

  let direction = $state<Direction>(page.params.direction ?? "indelible");
  const fx = $state({
    buttons: true,
    squircles: true,
    glass: true,
    shadows: true,
    blur: true,
    transitions: true,
  });

  const reducedMotion = new MediaQuery("prefers-reduced-motion: reduce");

  const view = new LabView();

  $effect(() => {
    view.transitions = fx.transitions && !reducedMotion.current;
  });

  function choose(next: Direction): void {
    view.transition(() => {
      direction = next;
    });
    void goto(resolve("/lab/[[direction=direction]]", { direction: next }), {
      replace: true,
      shallow: true,
    });
  }

  const effects = [
    ["buttons", copy.lab_buttons],
    ["squircles", copy.lab_squircles],
    ["glass", copy.lab_glass],
    ["shadows", copy.lab_shadows],
    ["blur", copy.lab_blur],
    ["transitions", copy.lab_transitions],
  ] as const;

  const themes = [
    ["system", copy.lab_system],
    ["light", copy.lab_light],
    ["dark", copy.lab_dark],
  ] as const;

  const about = $derived(
    directions.find(([key]) => key === direction)?.[2] ?? "",
  );
</script>

<svelte:head>
  <title>{copy.lab_title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link
    rel="preconnect"
    href="https://fonts.gstatic.com"
    crossorigin="anonymous"
  />
  <link
    rel="stylesheet"
    href="https://fonts.googleapis.com/css2?family=Literata:ital,opsz,wght@0,7..72,200..900;1,7..72,200..900&family=IBM+Plex+Sans:wdth,wght@75..100,100..700&family=IBM+Plex+Mono:wght@400;500;600&family=Piazzolla:opsz,wght@8..30,100..900&family=Sofia+Sans+Semi+Condensed:wght@1..1000&family=Martian+Mono:wdth,wght@75..112.5,100..800&family=Source+Serif+4:ital,opsz,wght@0,8..60,200..900;1,8..60,200..900&family=Inter:opsz,wght@14..32,100..900&family=JetBrains+Mono:wght@100..800&family=Noto+Naskh+Arabic:wght@400..700&display=swap"
  />
</svelte:head>

<div
  class="lab"
  data-direction={direction}
  data-theme={mode.current ?? "light"}
  data-buttons={fx.buttons ? "" : undefined}
  data-squircles={fx.squircles ? "" : undefined}
  data-glass={fx.glass ? "" : undefined}
  data-shadows={fx.shadows ? "" : undefined}
  data-blur={fx.blur ? "" : undefined}
>
  <div class="lab-bar">
    <div class="lab-group">
      <span class="lab-title">{copy.lab_title}</span>
      <div class="lab-tabs" role="tablist" aria-label={copy.lab_title}>
        {#each directions as [key, label] (key)}
          <button
            type="button"
            role="tab"
            class="lab-tab"
            aria-selected={direction === key}
            onclick={() => choose(key)}
          >
            {label}
          </button>
        {/each}
      </div>
      <span class="lab-about">{about}</span>
    </div>
    <div class="lab-group">
      <div class="lab-tabs" role="radiogroup" aria-label={copy.lab_theme}>
        {#each themes as [key, label] (key)}
          <button
            type="button"
            role="radio"
            class="lab-tab"
            aria-checked={userPrefersMode.current === key}
            onclick={() => view.transition(() => setMode(key))}
          >
            {label}
          </button>
        {/each}
      </div>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger class="lab-effects">
          {copy.lab_effects}
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="end" class="inline-56">
          <DropdownMenu.Group>
            <DropdownMenu.GroupHeading
              >{copy.lab_effects}</DropdownMenu.GroupHeading
            >
            {#each effects as [key, label] (key)}
              <DropdownMenu.CheckboxItem bind:checked={fx[key]}>
                {label}
              </DropdownMenu.CheckboxItem>
            {/each}
          </DropdownMenu.Group>
          {#if reducedMotion.current}
            <DropdownMenu.Separator />
            <p class="px-2 py-1.5 text-xs text-muted-foreground">
              {copy.lab_motion_note}
            </p>
          {/if}
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    </div>
  </div>

  {#if direction === "indelible"}
    <Indelible {view} />
  {:else if direction === "tally"}
    <Tally {view} motion={!reducedMotion.current} />
  {:else}
    <Chamber {view} motion={fx.transitions && !reducedMotion.current} />
  {/if}
</div>

<style>
  .lab-bar {
    position: sticky;
    inset-block-start: 0;
    z-index: 20;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px 16px;
    padding: 7px 14px;
    border-block-end: 1px solid rgb(128 128 128 / 0.35);
    background: rgb(128 128 128 / 0.16);
    backdrop-filter: blur(14px) saturate(1.2);
    color: var(--ink);
    font:
      12.5px/1.3 system-ui,
      sans-serif;
    font-stretch: 100%;
  }

  .lab-group {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 12px;
    min-inline-size: 0;
  }

  .lab-title {
    font-weight: 600;
  }

  .lab-tabs {
    display: inline-flex;
    padding: 2px;
    border-radius: 7px;
    background: rgb(128 128 128 / 0.18);
  }

  .lab-tab {
    padding: 3px 10px;
    border-radius: 5px;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }

  .lab-tab[aria-selected="true"],
  .lab-tab[aria-checked="true"] {
    background: var(--surface);
    box-shadow: 0 1px 2px rgb(0 0 0 / 0.15);
  }

  .lab-tab:focus-visible,
  :global(.lab-effects):focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }

  .lab-about {
    max-inline-size: 64ch;
    opacity: 0.75;
  }

  .lab-bar :global(.lab-effects) {
    padding: 3px 10px;
    border-radius: 7px;
    background: rgb(128 128 128 / 0.18);
    color: inherit;
    font: inherit;
  }

  .lab-bar :global(.lab-effects[data-state="open"]) {
    background: var(--surface);
    box-shadow: 0 1px 2px rgb(0 0 0 / 0.15);
  }
</style>
