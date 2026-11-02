<script lang="ts">
  import type { Snippet } from "svelte";
  import { afterNavigate, invalidate } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { CheckIcon } from "@lucide/svelte";
  import { onMount } from "svelte";
  import type { Latest } from "#lib/server/notifications.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { date } from "#lib/format.js";
  import { isNarrowLayout } from "#lib/narrow-layout.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";

  const {
    trigger,
    initial,
  }: { trigger: Snippet<[Record<string, unknown>]>; initial: boolean } =
    $props();

  const UNITS = [
    { unit: "minute", seconds: 60 },
    { unit: "hour", seconds: 3600 },
    { unit: "day", seconds: 86_400 },
  ] as const;

  let open = $state(false);
  let latest = $state<Latest | null>(null);
  let failed = $state(false);
  let request = 0;
  let content = $state<HTMLElement | null>(null);

  const narrow = $derived(isNarrowLayout());
  const unread = $derived(latest?.unread ?? page.data.unread);
  const relative = $derived(
    new Intl.RelativeTimeFormat(getLocale(), {
      numeric: "auto",
      style: "long",
    }),
  );

  function ago(at: string) {
    const seconds = (Date.now() - Date.parse(at)) / 1000;
    if (seconds < 60) {
      return m.notifications_menu_now();
    }
    if (seconds >= 7 * 86_400) {
      return date(at);
    }
    const found = UNITS.findLast((step) => seconds >= step.seconds) ?? UNITS[0];
    return relative.format(-Math.floor(seconds / found.seconds), found.unit);
  }

  async function refresh() {
    const ticket = ++request;
    failed = false;
    try {
      const response = await fetch("/notifications/latest");
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      const found: Latest = await response.json();
      if (ticket !== request) {
        return;
      }
      latest = found;
      if (found.unread !== page.data.unread) {
        await invalidate("ea:unread");
      }
    } catch {
      if (ticket === request) {
        failed = true;
      }
    }
  }

  async function post(action: "read" | "all", ids: Array<string> = []) {
    request += 1;
    const body = new FormData();
    for (const id of ids) {
      body.append("id", id);
    }
    await fetch(`/notifications?/${action}`, {
      method: "POST",
      body,
      keepalive: true,
      headers: { "x-sveltekit-action": "true" },
    });
    await (open ? refresh() : invalidate("ea:unread"));
  }

  function read(id: string) {
    if (latest == null) {
      return;
    }
    const entry = latest.entries.find((item) => item.id === id);
    if (entry == null || !entry.unread) {
      return;
    }
    entry.unread = false;
    latest.unread = Math.max(0, latest.unread - 1);
    void post("read", [id]);
  }

  function readAll() {
    if (latest == null) {
      return;
    }
    for (const entry of latest.entries) {
      entry.unread = false;
    }
    latest.unread = 0;
    void post("all");
  }

  function toggle(next: boolean) {
    open = next;
    if (next) {
      void refresh();
    }
  }

  onMount(() => {
    if (initial) {
      toggle(true);
    }
  });

  $effect(() => {
    if (open && narrow && content != null) {
      content.focus();
    }
  });

  afterNavigate(() => {
    open = false;
  });
</script>

{#snippet summary(entry: Latest["entries"][number])}
  {#if entry.unread}
    <span class="sr-only">{m.notifications_unread()}</span>
  {/if}
  <span class={cn("line-clamp-3 text-pretty", { "font-medium": entry.unread })}
    >{entry.text}</span
  >
  <span class="flex flex-wrap gap-x-2.5 text-xs text-muted-foreground">
    <time class="font-figures" datetime={entry.at}>{ago(entry.at)}</time>
    <span>{entry.byline}</span>
    {#if entry.polity != null}
      <span>{entry.polity}</span>
    {/if}
    {#if entry.watched}
      <span>{m.notifications_reason_watched()}</span>
    {/if}
  </span>
{/snippet}

{#snippet panel(heading: Snippet)}
  <div
    class="flex items-center justify-between gap-3 border-be px-4 py-3 max-md:pbs-1"
  >
    <div class="grid gap-0.5 min-inline-0">
      {@render heading()}
      <p class="text-xs text-muted-foreground" aria-live="polite">
        {#if latest != null}
          {unread > 0
            ? m.notifications_menu_unread({ count: unread })
            : m.notifications_menu_caught_up()}
        {/if}
      </p>
    </div>
    {#if unread > 0 && latest != null}
      <Button
        variant="outline"
        size="sm"
        onclick={(event: MouseEvent & { currentTarget: HTMLElement }) => {
          event.currentTarget.closest<HTMLElement>("[role='dialog']")?.focus();
          readAll();
        }}>{m.notifications_mark_all()}</Button
      >
    {/if}
  </div>
  {#if latest == null && failed}
    <div class="grid justify-items-start gap-2 px-4 py-5 text-sm">
      <p>{m.notifications_menu_error()}</p>
      <Button variant="outline" size="sm" onclick={refresh}
        >{m.notifications_menu_retry()}</Button
      >
    </div>
  {:else if latest == null}
    <ul class="grid" aria-busy="true">
      <li class="sr-only">{m.notifications_menu_loading()}</li>
      {#each [0, 1, 2] as row (row)}
        <li class="grid gap-2 border-be px-4 py-3 last:border-be-0">
          <span
            class="rounded-sm bg-foreground/12 block-3.5 inline-4/5 motion-safe:animate-pulse"
          ></span>
          <span
            class="rounded-sm bg-foreground/12 block-2.5 inline-2/5 motion-safe:animate-pulse"
          ></span>
        </li>
      {/each}
    </ul>
  {:else if latest.entries.length === 0}
    <p class="px-4 py-5 text-sm text-pretty text-muted-foreground">
      {m.notifications_menu_empty()}
    </p>
  {:else}
    <ol
      class="grid overflow-y-auto overscroll-contain md:max-block-[min(28rem,calc(100dvh-12rem))]"
    >
      {#each latest.entries as entry (entry.id)}
        <li
          class="flex items-start gap-1 border-be pe-2 last:border-be-0 has-[a:hover]:bg-accent/60"
        >
          <span
            class={cn(
              "ms-2 mbs-[1.15rem] shrink-0 rounded-full block-1.5 inline-1.5",
              { "bg-foreground": entry.unread },
            )}
            aria-hidden="true"
          ></span>
          {#if entry.href == null}
            <div
              tabindex="-1"
              class="grid flex-1 gap-1 py-3 ps-1.5 pe-1 text-sm outline-none min-inline-0"
            >
              {@render summary(entry)}
            </div>
          {:else}
            <a
              href={entry.href}
              class="grid flex-1 gap-1 rounded-sm py-3 ps-1.5 pe-1 text-sm min-inline-0 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
              onclick={() => {
                read(entry.id);
              }}
            >
              {@render summary(entry)}
            </a>
          {/if}
          {#if entry.unread}
            <Button
              variant="ghost"
              size="icon-sm"
              class="mbs-2 shrink-0 text-muted-foreground hover:text-foreground max-md:block-10 max-md:inline-10"
              aria-label={m.notifications_menu_mark_read()}
              title={m.notifications_menu_mark_read()}
              onclick={(event: MouseEvent & { currentTarget: HTMLElement }) => {
                event.currentTarget.parentElement
                  ?.querySelector<HTMLElement>("a, [tabindex]")
                  ?.focus();
                read(entry.id);
              }}
            >
              <CheckIcon />
            </Button>
          {/if}
        </li>
      {/each}
    </ol>
  {/if}
  <div
    class="flex items-center justify-between gap-3 border-bs px-4 py-2.5 text-sm max-md:pbe-4"
  >
    <a
      class="font-medium underline underline-offset-2"
      href={resolve("/notifications")}>{m.notifications_menu_view_all()}</a
    >
    <a
      class="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
      href={resolve("/settings/notifications")}>{m.notifications_settings()}</a
    >
  </div>
{/snippet}

{#snippet drawerHeading()}
  <Drawer.Title class="text-base font-semibold"
    >{m.notifications_title()}</Drawer.Title
  >
{/snippet}

{#snippet popoverHeading()}
  <h2 id="notifications-menu-title" class="font-semibold">
    {m.notifications_title()}
  </h2>
{/snippet}

{#if narrow}
  <Drawer.Root bind:open={() => open, toggle} shouldScaleBackground={false}>
    <Drawer.Trigger>
      {#snippet child({ props }: { props: Record<string, unknown> })}
        {@render trigger(props)}
      {/snippet}
    </Drawer.Trigger>
    <Drawer.Content
      class="sm:mx-auto sm:max-inline-xl"
      bind:ref={content}
      tabindex={-1}
    >
      {@render panel(drawerHeading)}
    </Drawer.Content>
  </Drawer.Root>
{:else}
  <Popover.Root bind:open={() => open, toggle}>
    <Popover.Trigger>
      {#snippet child({ props }: { props: Record<string, unknown> })}
        {@render trigger(props)}
      {/snippet}
    </Popover.Trigger>
    <Popover.Content
      align="end"
      sideOffset={6}
      bind:ref={content}
      tabindex={-1}
      onOpenAutoFocus={(event: Event) => {
        event.preventDefault();
        content?.focus();
      }}
      class="gap-0 overflow-hidden p-0 inline-[min(24rem,calc(100vw-2rem))]"
      role="dialog"
      aria-labelledby="notifications-menu-title"
    >
      {@render panel(popoverHeading)}
    </Popover.Content>
  </Popover.Root>
{/if}
