<script lang="ts">
  import Tabs from "#routes/review/tabs.svelte";
  import { resolve } from "$app/paths";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();
</script>

<svelte:head>
  <title>{m.accounts_title()} | {m.review_title()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-5xl max-sm:px-3">
  <h1 class="title">{m.review_title()}</h1>
  <Tabs current="accounts" />
  {#if !data.allowed}
    <p class="mbs-4 text-sm text-muted-foreground">{m.review_forbidden()}</p>
  {:else}
    <p class="max-inline-prose mbs-4 text-sm text-pretty text-muted-foreground">
      {m.accounts_lede()}
    </p>
    <form
      class="mbs-4 flex gap-2 max-inline-md"
      method="GET"
      role="search"
      aria-label={m.accounts_search_label()}
    >
      <Input
        name="q"
        type="search"
        value={data.q}
        placeholder={m.accounts_search_placeholder()}
        aria-label={m.accounts_search_label()}
        autocapitalize="none"
        spellcheck={false}
      />
      <Button type="submit" variant="outline">{m.accounts_search()}</Button>
    </form>
    {#if data.accounts.length === 0}
      <p class="mbs-4 border-bs pbs-3 text-sm text-muted-foreground">
        {m.accounts_empty()}
      </p>
    {:else}
      <ul class="mbs-4 grid border-bs text-sm">
        {#each data.accounts as row (row.id)}
          <li class="border-be">
            <a
              class="group grid gap-0.5 py-2.5"
              href={resolve("/review/accounts/[id]", { id: row.id })}
            >
              <span class="flex flex-wrap items-baseline gap-x-2">
                <span
                  class="font-semibold underline-offset-4 group-hover:underline"
                  >{row.name}</span
                >
                {#if row.handle != null}
                  <span class="text-muted-foreground">@{row.handle}</span>
                {/if}
                {#if row.banned}
                  <span class="text-xs font-semibold text-destructive"
                    >{m.account_banned()}</span
                  >
                {:else if row.limited}
                  <span class="text-xs font-semibold"
                    >{m.account_limited()}</span
                  >
                {/if}
              </span>
              <span
                class="flex flex-wrap gap-x-3 font-figures text-xs text-muted-foreground"
              >
                <span class="break-all">{row.email}</span>
                <span>{m.account_level({ level: String(row.level) })}</span>
                <span
                  >{m.account_tally({
                    accepted: row.accepted,
                    rejected: row.rejected,
                    reverted: row.reverted,
                  })}</span
                >
                <span
                  >{row.active_at == null
                    ? m.account_never_active()
                    : m.account_active({
                        date: date(row.active_at.slice(0, 10)),
                      })}</span
                >
              </span>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</main>
