<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { SearchIcon } from "@lucide/svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { finder } from "#lib/search.svelte.js";

  const COPY: Record<number, [() => string, () => string]> = {
    404: [m.error_404_title, m.error_404_body],
    410: [
      m.error_410_title,
      () => {
        const reason = page.error?.message ?? "";
        return m.error_410_body({
          reason: `${reason.charAt(0).toUpperCase()}${reason.slice(1)}${/[.!?]$/u.test(reason) ? "" : "."}`,
        });
      },
    ],
    429: [m.error_429_title, m.error_429_body],
  };
  const copy = $derived(
    COPY[page.status] ?? [m.error_500_title, m.error_500_body],
  );
  const guess = $derived(
    page.url.pathname
      .split("/")
      .findLast((part) => /[a-z]{3}/iu.test(part))
      ?.replaceAll(/[-_]+/gu, " ")
      .replaceAll(/\b(?:county|general|local|\d{4}(?: \d{2}){0,2})\b/giu, "")
      .trim() ?? "",
  );
</script>

<svelte:head>
  <title>{copy[0]()} | {m.wordmark()}</title>
</svelte:head>

<main
  class="grid content-start gap-4 px-4 pbs-16 pbe-24 max-inline-160 sm:pbs-28"
  aria-labelledby="error-title"
>
  <p class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums">
    {m.error_code({ code: String(page.status) })}
  </p>
  <h1 id="error-title" class="title">
    {copy[0]()}
  </h1>
  <p class="text-[1.0625rem]/relaxed text-pretty text-muted-foreground">
    {copy[1]()}
  </p>
  <div class="flex flex-wrap items-center gap-3 pbs-2">
    {#if page.status !== 429}
      <button
        type="button"
        class="flex items-center gap-2 rounded-sm border border-foreground bg-marker px-3.5 text-sm font-semibold text-[#15171a] block-10 hover:bg-[color-mix(in_oklab,var(--marker),#15171a_10%)]"
        onclick={() => {
          finder.value = guess;
          finder.isOpen = true;
        }}
      >
        <SearchIcon class="block-4 inline-4" />{m.error_search()}
      </button>
    {/if}
    <a
      class="text-sm font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
      href={resolve("/")}>{m.error_home()}</a
    >
  </div>
</main>
