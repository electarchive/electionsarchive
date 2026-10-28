<script lang="ts">
  import Name from "#lib/components/name.svelte";
  import { resolve } from "$app/paths";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";
  import Balance from "./balance.svelte";

  const {
    slate,
    last,
    held,
  }: {
    slate: NonNullable<
      Extract<PageData, { kind: "contest" }>["contest"]["slate"]
    >;
    last: Extract<PageData, { kind: "contest" }>["contest"]["last"];
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
  {#if slate.isUnqualified}
    <p class="text-[0.9375rem] font-semibold">{m.slate_unqualified()}</p>
  {/if}
  <div
    class="grid items-start gap-x-10 gap-y-5 lg:grid-cols-[minmax(0,40rem)_minmax(0,22rem)]"
  >
    {#if slate.options.length > 0}
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
    {/if}
    {#if last != null}
      <aside class="grid gap-3" aria-labelledby="{uid}-last">
        <div class="grid gap-0.5">
          <h3 id="{uid}-last" class="text-[0.8125rem] font-bold">
            {m.slate_last()}
          </h3>
          {#if last.path == null}
            <span class="text-[0.8125rem] text-muted-foreground"
              >{m.slate_last_link({ date: date(last.date) })}</span
            >
          {:else}
            <a
              class="text-[0.8125rem] text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-foreground"
              href={resolve("/[...path]", { path: last.path })}
              >{m.slate_last_link({ date: date(last.date) })}</a
            >
          {/if}
        </div>
        <Balance
          parts={last.rows.map((row) => ({
            ...row,
            color: swatch(row.color, row.key),
            value: row.share,
          }))}
          total={1}
          isCount={false}
        />
      </aside>
    {/if}
  </div>
</section>
