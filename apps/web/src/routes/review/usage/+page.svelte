<script lang="ts">
  import Tabs from "#routes/review/tabs.svelte";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  function day(value: string | null) {
    return date(value?.slice(0, 10) ?? null);
  }
</script>

<svelte:head>
  <title>{m.usage_title()} | {m.review_title()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

{#snippet head(labels: Array<string>)}
  <thead>
    <tr class="border-be text-muted-foreground">
      {#each labels as label, index (label)}
        <th
          class="py-2 font-normal"
          class:text-start={index === 0}
          class:text-end={index > 0}
          class:pe-4={index < labels.length - 1}>{label}</th
        >
      {/each}
    </tr>
  </thead>
{/snippet}

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-5xl max-sm:px-3">
  <h1 class="title">{m.review_title()}</h1>
  <Tabs current="usage" />
  {#if !data.allowed}
    <p class="mbs-4 text-sm text-muted-foreground">{m.usage_forbidden()}</p>
  {:else if data.report == null}
    <p class="mbs-4 text-sm text-muted-foreground">{m.usage_unavailable()}</p>
  {:else}
    {const report = $derived(data.report)}
    <p class="max-inline-prose mbs-4 text-sm text-pretty text-muted-foreground">
      {m.usage_lede()}
    </p>
    {#if report.totals != null}
      <dl
        class="mbs-6 grid grid-cols-2 gap-x-6 gap-y-4 border-bs pbs-4 sm:grid-cols-4"
      >
        <div class="grid content-start gap-0.5">
          <dt class="text-xs text-muted-foreground">{m.usage_today()}</dt>
          <dd class="font-figures text-2xl font-semibold">
            {count(report.totals.today)}
          </dd>
        </div>
        <div class="grid content-start gap-0.5">
          <dt class="text-xs text-muted-foreground">{m.usage_month()}</dt>
          <dd class="font-figures text-2xl font-semibold">
            {count(report.totals.month)}
          </dd>
        </div>
        <div class="grid content-start gap-0.5">
          <dt class="text-xs text-muted-foreground">
            {m.usage_anonymous_today()}
          </dt>
          <dd class="font-figures text-2xl font-semibold">
            {count(report.totals.anonymous_today)}
          </dd>
          <dd class="text-xs text-muted-foreground">
            {m.usage_anonymous_callers({
              count: count(report.totals.anonymous_callers),
            })}
          </dd>
        </div>
        <div class="grid content-start gap-0.5">
          <dt class="text-xs text-muted-foreground">
            {m.usage_anonymous_month()}
          </dt>
          <dd class="font-figures text-2xl font-semibold">
            {count(report.totals.anonymous_month)}
          </dd>
        </div>
      </dl>
    {/if}

    <section class="mbs-10 grid gap-3" aria-labelledby="usage-keys">
      <h2 id="usage-keys" class="heading">{m.usage_keys()}</h2>
      {#if report.keys.length === 0}
        <p class="border-bs pbs-3 text-sm text-muted-foreground">
          {m.usage_none()}
        </p>
      {:else}
        <table class="border-bs text-sm inline-full">
          {@render head([
            m.usage_col_key(),
            m.usage_col_today(),
            m.usage_col_month(),
            m.usage_col_last(),
          ])}
          <tbody>
            {#each report.keys as row (row.key_id)}
              <tr class="border-be align-baseline">
                <td class="py-2 pe-4">
                  <span class="font-semibold">{row.name ?? "–"}</span>
                  <span class="block text-xs text-muted-foreground"
                    ><span class="font-figures">{row.start ?? ""}…</span>
                    {row.owner ?? ""}</span
                  >
                </td>
                <td class="py-2 pe-4 text-end font-figures"
                  >{count(row.today)}</td
                >
                <td class="py-2 pe-4 text-end font-figures"
                  >{count(row.month)}</td
                >
                <td class="py-2 text-end font-figures whitespace-nowrap"
                  >{day(row.last_at)}</td
                >
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    <section class="mbs-10 grid gap-3" aria-labelledby="usage-users">
      <h2 id="usage-users" class="heading">{m.usage_users()}</h2>
      {#if report.users.length === 0}
        <p class="border-bs pbs-3 text-sm text-muted-foreground">
          {m.usage_none()}
        </p>
      {:else}
        <table class="border-bs text-sm inline-full">
          {@render head([
            m.usage_col_user(),
            m.usage_col_keys(),
            m.usage_col_today(),
            m.usage_col_month(),
          ])}
          <tbody>
            {#each report.users as row (row.user_id)}
              <tr class="border-be align-baseline">
                <td class="py-2 pe-4 font-semibold">{row.name ?? "–"}</td>
                <td class="py-2 pe-4 text-end font-figures"
                  >{count(row.keys)}</td
                >
                <td class="py-2 pe-4 text-end font-figures"
                  >{count(row.today)}</td
                >
                <td class="py-2 text-end font-figures">{count(row.month)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    <div class="mbs-10 grid gap-10 md:grid-cols-2">
      <section class="grid content-start gap-3" aria-labelledby="usage-clients">
        <h2 id="usage-clients" class="heading">{m.usage_clients()}</h2>
        {#if report.clients.length === 0}
          <p class="border-bs pbs-3 text-sm text-muted-foreground">
            {m.usage_none()}
          </p>
        {:else}
          <table class="border-bs text-sm inline-full">
            {@render head([
              m.usage_col_client(),
              m.usage_col_callers(),
              m.usage_col_today(),
              m.usage_col_month(),
            ])}
            <tbody>
              {#each report.clients as row (row.client)}
                <tr class="border-be align-baseline">
                  <td class="py-2 pe-4 break-all">{row.client}</td>
                  <td class="py-2 pe-4 text-end font-figures"
                    >{count(row.callers)}</td
                  >
                  <td class="py-2 pe-4 text-end font-figures"
                    >{count(row.today)}</td
                  >
                  <td class="py-2 text-end font-figures">{count(row.month)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>

      <section class="grid content-start gap-3" aria-labelledby="usage-tools">
        <h2 id="usage-tools" class="heading">{m.usage_tools()}</h2>
        {#if report.tools.length === 0}
          <p class="border-bs pbs-3 text-sm text-muted-foreground">
            {m.usage_none()}
          </p>
        {:else}
          <table class="border-bs text-sm inline-full">
            {@render head([
              m.usage_col_tool(),
              m.usage_col_today(),
              m.usage_col_month(),
            ])}
            <tbody>
              {#each report.tools as row (row.tool)}
                <tr class="border-be align-baseline">
                  <td class="py-2 pe-4 font-figures">{row.tool}</td>
                  <td class="py-2 pe-4 text-end font-figures"
                    >{count(row.today)}</td
                  >
                  <td class="py-2 text-end font-figures">{count(row.month)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    </div>
  {/if}
</main>
