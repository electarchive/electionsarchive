<script lang="ts">
  import { resolve } from "$app/paths";
  import { count, date, definite } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import Preview from "./preview.svelte";

  const {
    day,
  }: {
    day: {
      date: string | null;
      place: string;
      part: { name: string; path: string | null } | null;
      groups: Array<{
        key: string;
        label: string;
        count: number;
        path: string;
        anchor: string | null;
      }>;
    };
  } = $props();

  const uid = $props.id();
</script>

{#if day.groups.length > 0}
  <section
    class="col-span-12 grid content-start gap-2 bg-card px-3.5 pbs-3 pbe-3.5"
    aria-labelledby="{uid}-day"
  >
    <h2 id="{uid}-day" class="text-[0.8125rem] font-bold">
      {m.cycle_day({ date: date(day.date), place: definite(day.place) })}
    </h2>
    <ul
      class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,11.5rem),1fr))] gap-x-4 gap-y-1.5 border-bs-2 border-border pbs-2"
    >
      {#if day.part?.path != null}
        <li class="col-span-full text-sm">
          <Preview
            class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
            path={day.part.path}>{day.part.name}</Preview
          >
        </li>
      {/if}
      {#each day.groups as group (group.key)}
        <li>
          <a
            class="group grid grid-cols-[3.25rem_minmax(0,1fr)] items-baseline gap-x-2 text-[0.8125rem]/snug"
            href="{resolve('/[...path]', { path: group.path })}{group.anchor ==
            null
              ? ''
              : `#${group.anchor}`}"
          >
            <span
              class="text-end font-figures text-[0.9375rem] font-semibold tabular-nums"
              >{group.count > 1 ? count(group.count) : ""}</span
            >
            <span
              class="underline decoration-border underline-offset-4 group-hover:decoration-foreground"
              >{group.label}</span
            >
          </a>
        </li>
      {/each}
    </ul>
  </section>
{/if}
