<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";
  import Balance from "./balance.svelte";
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
      <li class="grid content-start gap-3">
        <p class="text-sm font-semibold text-pretty">
          <Preview
            class="underline decoration-border underline-offset-4 hover:decoration-foreground"
            path={race.path}>{race.name}</Preview
          >
        </p>
        <Balance
          parts={race.leaders.map((leader) => ({
            ...leader,
            color: swatch(leader.color, leader.key),
            value: leader.share,
          }))}
          total={1}
          isCount={false}
        />
      </li>
    {/each}
  </ul>
</section>
