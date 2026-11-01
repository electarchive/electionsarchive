<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";
  import Preview from "./preview.svelte";

  const {
    place,
    races,
  }: {
    place: string;
    races: Array<{
      id: string;
      name: string;
      path: string;
      isCurrent: boolean;
    }>;
  } = $props();

  const uid = $props.id();
</script>

<nav
  class="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm md:col-span-2"
  aria-labelledby="{uid}-title"
>
  <h2 id="{uid}-title" class="text-[0.8125rem] font-bold">
    {m.same_day_title({ place })}
  </h2>
  <ul class="contents">
    {#each races as race (race.path)}
      <li>
        {#if race.isCurrent}
          <span
            class="-mx-0.5 bg-marker/45 px-0.5 font-semibold dark:bg-marker dark:text-[#15171a]"
            aria-current="page">{race.name}</span
          >
        {:else}
          <Preview
            class="underline decoration-border underline-offset-4 hover:decoration-foreground"
            path={race.path}>{race.name}</Preview
          >
        {/if}
      </li>
    {/each}
  </ul>
</nav>
