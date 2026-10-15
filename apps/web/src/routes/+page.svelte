<script lang="ts">
  import { resolve } from "$app/paths";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();
</script>

<main class="p-3.5 max-sm:p-0">
  <article
    class="grid grid-cols-12 gap-px overflow-hidden rounded-lg border bg-border max-sm:rounded-none max-sm:border-x-0"
  >
    <header class="col-span-12 bg-card px-3.5 pbs-5 pbe-4">
      <h1
        class="font-serif text-[clamp(1.75rem,3.4vw,2.75rem)] leading-[1.04] font-[640] tracking-[-0.01em] text-balance max-inline-[24ch]"
      >
        {m.home_intro()}
      </h1>
    </header>
    <section class="col-span-12 bg-card px-3.5 py-3">
      <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.home_polities()}</h2>
      <ul class="grid gap-x-6 sm:grid-cols-2 lg:grid-cols-3">
        {#each data.polities as polity (polity.id)}
          <li
            class="flex items-baseline justify-between gap-4 border-be border-border/60 py-2"
          >
            {#if polity.path == null}
              <span class="font-semibold">{polity.name}</span>
            {:else}
              <a
                class="font-semibold hover:highlighter"
                href={resolve("/[...path]", { path: polity.path })}
                >{polity.name}</a
              >
            {/if}
            <span class="shrink-0 text-[0.8125rem] text-muted-foreground"
              >{m.home_elections({ count: polity.elections })}</span
            >
          </li>
        {/each}
      </ul>
    </section>
  </article>
</main>
