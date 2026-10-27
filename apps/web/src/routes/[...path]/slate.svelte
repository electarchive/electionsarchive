<script lang="ts">
  import Name from "#lib/components/name.svelte";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";

  const {
    slate,
    held,
  }: {
    slate: NonNullable<
      Extract<PageData, { kind: "contest" }>["contest"]["slate"]
    >;
    held: string | null;
  } = $props();

  const uid = $props.id();
  const STATES = {
    scheduled: m.slate_scheduled,
    counting: m.slate_counting,
    unreported: m.slate_unreported,
  };
</script>

<section
  class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5"
  aria-labelledby="{uid}-title"
>
  <div class="grid gap-1">
    <h2 id="{uid}-title" class="text-[0.8125rem] font-bold">
      {m.slate_title()}
    </h2>
    <p class="text-sm text-pretty text-muted-foreground">
      {STATES[slate.state]({ date: date(held) })}
    </p>
  </div>
  <ul
    class="grid divide-y divide-border border-y border-border max-inline-[40rem]"
  >
    {#each slate.options as option (option.key)}
      <li class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2">
        <Name
          class="font-semibold min-inline-0"
          label={option.label}
          color={option.party == null
            ? null
            : swatch(option.party.color, option.party.id)}
          person={option.person}
          party={option.party?.id ?? null}
        />
        {#if option.isIncumbent}
          <span class="highlighter px-1 text-xs font-semibold"
            >{m.slate_incumbent()}</span
          >
        {/if}
        <span class="ms-auto text-sm text-muted-foreground">
          {#if option.party != null}
            {option.party.short ?? option.party.name}
          {:else if option.isIndependent}
            {m.slate_independent()}
          {/if}
        </span>
      </li>
    {/each}
  </ul>
</section>
