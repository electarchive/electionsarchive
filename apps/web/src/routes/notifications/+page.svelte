<script lang="ts">
  import { enhance } from "$app/forms";
  import { invalidate } from "$app/navigation";
  import { onMount } from "svelte";
  import FeedList from "#lib/components/contrib/feed-list.svelte";
  import FilterNav from "#lib/components/contrib/filter-nav.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  const empty = {
    all: m.notifications_empty_all,
    direct: m.notifications_empty_direct,
    watched: m.notifications_empty_watched,
  };

  const older = $derived(
    data.next == null
      ? null
      : `/notifications?${new URLSearchParams({
          ...(data.show === "all" ? {} : { show: data.show }),
          before: data.next,
        }).toString()}`,
  );

  onMount(() => {
    const body = new FormData();
    for (const [id, mark] of Object.entries(data.marks)) {
      if (mark.unread) {
        body.append("id", id);
      }
    }
    if (!body.has("id")) {
      return;
    }
    void fetch("/notifications?/read", {
      method: "POST",
      body,
      headers: { "x-sveltekit-action": "true" },
    }).then(async () => invalidate("ea:unread"));
  });
</script>

<svelte:head>
  <title>{m.notifications_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
      <h1 class="title">{m.notifications_title()}</h1>
      <div class="flex gap-4 text-sm">
        <a class="underline underline-offset-2" href="/settings/notifications"
          >{m.notifications_settings()}</a
        >
        <a class="underline underline-offset-2" href="/watchlist"
          >{m.notifications_watchlist()}</a
        >
      </div>
    </div>
    <p class="max-inline-prose text-sm text-pretty text-muted-foreground">
      {m.notifications_lede()}
    </p>
    <div class="flex flex-wrap items-center justify-between gap-3">
      <FilterNav
        label={m.notifications_show()}
        name="show"
        options={[
          { value: null, label: m.notifications_show_all() },
          { value: "direct", label: m.notifications_show_direct() },
          { value: "watched", label: m.notifications_show_watched() },
        ]}
      />
      <form method="POST" action="?/all" use:enhance>
        <Button type="submit" variant="outline" size="sm"
          >{m.notifications_mark_all()}</Button
        >
      </form>
    </div>
    <p class="text-xs text-muted-foreground" role="status">
      {#if form != null && "marked" in form}{m.notifications_marked()}{/if}
    </p>
  </header>

  <section class="mbs-4">
    {#if data.entries.length === 0}
      <p class="border-bs pbs-3 text-sm text-pretty text-muted-foreground">
        {empty[data.show]()}
      </p>
    {:else}
      <FeedList
        entries={data.entries}
        label={m.notifications_title()}
        marks={data.marks}
      />
    {/if}
    {#if older != null}
      <p class="mbs-4 text-sm">
        <a class="underline underline-offset-2" href={older}
          >{m.recent_older()}</a
        >
      </p>
    {/if}
  </section>
</main>
