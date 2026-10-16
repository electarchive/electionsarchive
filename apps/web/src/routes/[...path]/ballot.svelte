<script lang="ts">
  import Name from "#lib/components/name.svelte";
  import { percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";
  import Preview from "./preview.svelte";

  const {
    ballot,
    place,
  }: {
    ballot: Extract<PageData, { kind: "contest" }>["ballot"];
    place: string;
  } = $props();

  const uid = $props.id();
</script>

<section
  class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5"
  aria-labelledby="{uid}-title"
>
  <h2 id="{uid}-title" class="text-[0.8125rem] font-bold">
    {m.ballot_title({ place })}
  </h2>
  <ul
    class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,17rem),1fr))] gap-x-6 gap-y-4"
  >
    {#each ballot as race (race.id)}
      <li class="grid content-start gap-1.5">
        <p class="text-sm font-semibold text-pretty">
          <Preview
            class="underline decoration-border underline-offset-4 hover:decoration-foreground"
            path={race.path}>{race.name}</Preview
          >
        </p>
        <div
          class="flex overflow-hidden rounded-xs bg-muted block-2"
          aria-hidden="true"
        >
          {#each race.leaders as leader (leader.key)}
            <span
              class="block-full"
              style:inline-size="{leader.share * 100}%"
              style:background-color={swatch(leader.color, leader.key)}
            ></span>
          {/each}
        </div>
        <ul class="grid gap-0.5 text-sm">
          {#each race.leaders as leader (leader.key)}
            <li
              class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3"
            >
              <Name
                class="truncate min-inline-0"
                label={leader.label}
                color={swatch(leader.color, leader.key)}
                person={leader.person}
                party={leader.party}
              />
              <span class="font-mono text-[0.8125rem] tabular-nums"
                >{percent(leader.share)}</span
              >
            </li>
          {/each}
        </ul>
      </li>
    {/each}
  </ul>
</section>
