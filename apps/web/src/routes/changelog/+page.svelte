<script lang="ts">
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { area, REPOSITORY } from "#lib/site.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();
</script>

<svelte:head>
  <title>{m.changelog_title()}</title>
  <link
    rel="alternate"
    type="application/atom+xml"
    title={m.changelog_title()}
    href="/changelog.atom"
  />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <h1 class="title">{m.changelog_title()}</h1>
    <p class="max-inline-prose text-sm text-pretty text-muted-foreground">
      {m.changelog_lede()}
    </p>
  </header>

  {#if data.versions.length === 0}
    <p class="mbs-8 text-sm text-muted-foreground">{m.changelog_empty()}</p>
  {:else}
    <ol class="mbs-8 grid border-bs">
      {#each data.versions as version, index (version.version)}
        <li
          id="v{version.version}"
          class="grid scroll-mbs-20 gap-2 border-be py-4"
        >
          <div
            class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 font-figures tabular-nums"
          >
            <h2 class="text-[1.0625rem] font-[720]">{version.version}</h2>
            <span class="text-[0.8125rem] text-muted-foreground"
              >{date(version.date.slice(0, 10))}</span
            >
            <a
              href="{REPOSITORY}/commit/{version.commit}"
              rel="external noopener"
              class="text-[0.8125rem] text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-current"
              >{version.commit.slice(0, 7)}</a
            >
          </div>
          {#if version.commits.length === 0}
            {#if index === data.versions.length - 1}
              <p class="text-sm text-muted-foreground">
                {m.changelog_first()}
              </p>
            {/if}
          {:else}
            <details open={index === 0}>
              <summary
                class="cursor-pointer text-[0.8125rem] font-semibold text-muted-foreground select-none hover:text-foreground"
                >{m.changelog_commits({
                  count: version.commits.length,
                })}</summary
              >
              <ul class="mbs-2 grid gap-1.5 text-sm/snug max-sm:gap-3">
                {#each version.commits as commit (commit.sha)}
                  {const parts = $derived(area(commit.subject))}
                  <li
                    class="grid grid-cols-[7rem_1fr_auto] items-baseline gap-x-3 max-sm:grid-cols-[1fr_auto]"
                  >
                    <span
                      class="truncate font-figures text-xs text-muted-foreground max-sm:col-span-2"
                      >{parts.area ?? ""}</span
                    >
                    <span class="text-pretty">{parts.text}</span>
                    <a
                      href="{REPOSITORY}/commit/{commit.sha}"
                      rel="external noopener"
                      class="font-figures text-xs text-muted-foreground tabular-nums underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-current"
                      >{commit.sha.slice(0, 7)}</a
                    >
                  </li>
                {/each}
              </ul>
            </details>
          {/if}
        </li>
      {/each}
    </ol>
  {/if}

  <p class="mbs-6 text-sm">
    <a
      href="{REPOSITORY}/commits/main"
      rel="external noopener"
      class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
      >{m.changelog_history()}</a
    >
  </p>
</main>
