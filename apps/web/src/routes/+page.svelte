<script lang="ts">
  import { resolve } from "$app/paths";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const totals = $derived.by(() => {
    let elections = 0;
    const years: Array<number> = [];
    for (const polity of data.polities) {
      elections += polity.elections + polity.within;
      years.push(...[polity.first, polity.last].filter((year) => year != null));
    }
    return {
      elections,
      first: years.length === 0 ? null : Math.min(...years),
      last: years.length === 0 ? null : Math.max(...years),
    };
  });
  const listed = $derived(
    data.polities.toSorted(
      (a, b) =>
        Number(b.elections + b.within > 0) -
          Number(a.elections + a.within > 0) || a.name.localeCompare(b.name),
    ),
  );
</script>

<main class="p-3.5 max-sm:p-0">
  <article
    class="grid grid-cols-12 gap-px overflow-hidden rounded-lg border bg-border max-sm:rounded-none max-sm:border-x-0"
  >
    <header class="col-span-12 grid gap-2 bg-card px-3.5 pbs-5 pbe-4">
      <h1
        class="font-serif text-[clamp(1.75rem,3.4vw,2.75rem)] leading-[1.04] font-[640] tracking-[-0.01em] text-balance max-inline-[24ch]"
      >
        {m.home_intro()}
      </h1>
      {#if totals.first != null && totals.last != null}
        <p
          class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[60ch]"
        >
          {m.home_lede({
            elections: count(totals.elections),
            first: String(totals.first),
            last: String(totals.last),
          })}
        </p>
      {/if}
    </header>
    <section class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-4">
      <h2 class="text-[0.8125rem] font-bold">{m.home_polities()}</h2>
      <ul
        class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,16rem),1fr))] gap-px overflow-hidden rounded-md border"
      >
        {#each listed as polity (polity.id)}
          <li
            class="grid content-start gap-1 bg-card px-3 pbs-2.5 pbe-3 shadow-[0_0_0_1px_var(--border)]"
          >
            {#if polity.path == null}
              <span class="font-serif text-lg/tight font-[640]"
                >{polity.name}</span
              >
            {:else}
              <a
                class="justify-self-start font-serif text-lg/tight font-[640] hover:highlighter"
                href={resolve("/[...path]", { path: polity.path })}
                >{polity.name}</a
              >
            {/if}
            <p class="text-[0.8125rem] text-muted-foreground">
              {m.home_elections({
                count: polity.elections,
              })}{#if polity.first != null && polity.last != null}<span
                  class="ms-2 font-mono text-[0.6875rem] tabular-nums"
                  >{polity.first === polity.last
                    ? polity.first
                    : m.polity_span({
                        first: String(polity.first),
                        last: String(polity.last),
                      })}</span
                >{/if}{#if polity.within > 0}<br />{m.polity_within({
                  count: count(polity.within),
                })}{/if}
            </p>
            {#if polity.latest?.path != null}
              <p class="grid gap-0.5 pbs-1 text-[0.8125rem]">
                <span class="text-xs text-muted-foreground"
                  >{m.home_latest()}</span
                >
                <a
                  class="justify-self-start underline decoration-border underline-offset-4 hover:decoration-foreground"
                  href={resolve("/[...path]", { path: polity.latest.path })}
                  >{polity.latest.name}</a
                >
              </p>
            {/if}
          </li>
        {/each}
      </ul>
    </section>
  </article>
</main>
