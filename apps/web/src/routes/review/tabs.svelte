<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { current }: { current: "queue" | "accounts" | "usage" } = $props();

  const tabs = $derived([
    { key: "queue", href: resolve("/review"), label: m.review_tab_queue() },
    {
      key: "accounts",
      href: resolve("/review/accounts"),
      label: m.review_tab_accounts(),
    },
    ...(page.data.admin === true
      ? [
          {
            key: "usage",
            href: resolve("/review/usage"),
            label: m.review_tab_usage(),
          },
        ]
      : []),
  ]);
</script>

<nav class="mbs-3 flex gap-5 border-be text-sm" aria-label={m.review_title()}>
  {#each tabs as tab (tab.key)}
    <a
      class={cn("-mbe-px border-be-2 py-2", {
        "border-foreground font-semibold": tab.key === current,
        "border-transparent text-muted-foreground hover:text-foreground":
          tab.key !== current,
      })}
      href={tab.href}
      aria-current={tab.key === current ? "page" : undefined}>{tab.label}</a
    >
  {/each}
</nav>
