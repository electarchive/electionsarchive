<script lang="ts">
  import { resolve } from "$app/paths";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const {
    counties,
  }: {
    counties: NonNullable<
      Extract<PageData, { kind: "election" }>["sections"]["counties"]
    >;
  } = $props();

  const uid = $props.id();

  const total = $derived(
    counties.counties.flatMap((county) => county.governments).length,
  );
</script>

{#if counties.counties.length > 0}
  <section
    class="col-span-12 grid content-start gap-4 bg-card px-3.5 pbs-4 pbe-4"
    aria-labelledby="{uid}-title"
  >
    <div class="grid gap-1">
      <h2
        id="{uid}-title"
        class="text-2xl/tight font-[760] tracking-[-0.015em] text-balance"
      >
        {m.counties_title()}
      </h2>
      <p class="text-[0.9375rem] text-pretty max-inline-[64ch]">
        {m.counties_lede({
          governments: m.local_governments({ count: total }),
          counties: m.local_counties({ count: counties.counties.length }),
          contests: m.race_contests({ count: counties.contests }),
        })}
      </p>
    </div>
    <ol class="columns-[20rem] gap-x-8 text-sm">
      {#each counties.counties as county (county.path ?? county.name)}
        <li
          class="grid break-inside-avoid gap-0.5 border-be border-border/60 py-2 pointer-coarse:py-2.5"
        >
          <span class="flex items-baseline justify-between gap-3">
            {#if county.path == null}
              <span class="font-semibold">{county.name}</span>
            {:else}
              <a
                class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
                href={resolve("/[...path]", { path: county.path })}
                >{county.name}</a
              >
            {/if}
            <span class="shrink-0 text-[0.8125rem] text-muted-foreground"
              >{m.local_governments({ count: county.governments.length })}</span
            >
          </span>
          <span class="text-[0.8125rem] text-pretty text-muted-foreground">
            {#each county.governments.slice(0, 4) as government, index (index)}
              <span
                >{#if government.path == null}{government.name}{:else}<a
                    class="underline decoration-transparent underline-offset-2 hover:text-foreground hover:decoration-foreground"
                    href={resolve("/[...path]", { path: government.path })}
                    >{government.name}</a
                  >{/if}{index < Math.min(4, county.governments.length) - 1
                  ? ", "
                  : county.governments.length > 4
                    ? `, ${m.counties_more({ count: String(county.governments.length - 4) })}`
                    : ""}</span
              >
            {/each}
          </span>
        </li>
      {/each}
    </ol>
  </section>
{/if}
