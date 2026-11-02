<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    entity,
    current,
  }: { entity: { id: string; public_id: string }; current: "talk" } = $props();

  const tabs = $derived([
    { key: "record", href: `/id/${entity.id}`, label: m.talk_tab_record() },
    {
      key: "talk",
      href: `/talk/${entity.public_id}`,
      label: m.talk_tab_talk(),
    },
  ]);
</script>

<nav aria-label={m.talk_tabs()} class="border-be">
  <ul class="flex gap-5 text-sm">
    {#each tabs as tab (tab.key)}
      <li>
        <a
          class={cn(
            "-mbe-px inline-block border-be-2 py-2 underline-offset-4 hover:underline",
            {
              "border-foreground font-semibold": tab.key === current,
              "border-transparent text-muted-foreground hover:text-foreground":
                tab.key !== current,
            },
          )}
          href={tab.href}
          aria-current={tab.key === current ? "page" : undefined}>{tab.label}</a
        >
      </li>
    {/each}
  </ul>
</nav>
