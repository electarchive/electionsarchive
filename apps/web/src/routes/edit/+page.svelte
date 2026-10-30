<script lang="ts">
  import { enhance } from "$app/forms";
  import { resolve } from "$app/paths";
  import { Button } from "#lib/components/ui/button/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  let isStarting = $state(false);
</script>

<svelte:head>
  <title>{m.ws_index_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-10 px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-2">
    <h1 class="title">{m.ws_index_title()}</h1>
    <p
      class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[60ch]"
    >
      {m.ws_index_intro()}
    </p>
  </header>

  <section
    class="grid gap-3 border-bs-[3px] border-foreground pbs-3"
    aria-labelledby="templates"
  >
    <h2 id="templates" class="heading">{m.ws_templates()}</h2>
    <form
      method="POST"
      action="?/start"
      class="flex items-start justify-between gap-6 border-be pbe-4 max-sm:flex-col max-sm:gap-3"
      use:enhance={() => {
        isStarting = true;
        return async ({ update }) => {
          await update();
          isStarting = false;
        };
      }}
    >
      <div class="grid gap-1">
        <h3 class="font-semibold">{m.ws_template_identifiers()}</h3>
        <p class="text-sm text-pretty text-muted-foreground max-inline-[52ch]">
          {m.ws_template_identifiers_body()}
        </p>
      </div>
      <Button type="submit" disabled={isStarting}
        >{m.ws_template_start()}</Button
      >
    </form>
  </section>

  <section
    class="grid gap-3 border-bs-[3px] border-foreground pbs-3"
    aria-labelledby="drafts"
  >
    <h2 id="drafts" class="heading">{m.ws_drafts()}</h2>
    {#if data.drafts.length === 0}
      <p class="text-sm text-muted-foreground">{m.ws_drafts_empty()}</p>
    {:else}
      <ul class="grid">
        {#each data.drafts as draft (draft.number)}
          <li>
            <a
              class="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-baseline gap-x-3 border-be py-2 hover:bg-muted max-sm:grid-cols-[4rem_minmax(0,1fr)]"
              href={resolve("/edit/[number]", { number: String(draft.number) })}
            >
              <span class="font-figures text-muted-foreground"
                >#{draft.number}</span
              >
              <span class="truncate font-semibold">{draft.title}</span>
              <span
                class="font-figures text-sm text-muted-foreground max-sm:col-start-2"
                >{m.ws_draft_rows({ count: draft.rows })} · {m.ws_draft_saved_on(
                  { date: date(draft.updated_at.slice(0, 10)) },
                )}</span
              >
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
</main>
