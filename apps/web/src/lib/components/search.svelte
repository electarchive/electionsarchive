<script lang="ts">
  import { afterNavigate, goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import BlocksIcon from "@lucide/svelte/icons/blocks";
  import ClockIcon from "@lucide/svelte/icons/clock";
  import EyeIcon from "@lucide/svelte/icons/eye";
  import FlagIcon from "@lucide/svelte/icons/flag";
  import HouseIcon from "@lucide/svelte/icons/house";
  import LandmarkIcon from "@lucide/svelte/icons/landmark";
  import LinkIcon from "@lucide/svelte/icons/link";
  import MapPinIcon from "@lucide/svelte/icons/map-pin";
  import MonitorIcon from "@lucide/svelte/icons/monitor";
  import MoonIcon from "@lucide/svelte/icons/moon";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SunIcon from "@lucide/svelte/icons/sun";
  import UserIcon from "@lucide/svelte/icons/user";
  import VoteIcon from "@lucide/svelte/icons/vote";
  import { createQuery, keepPreviousData } from "@tanstack/svelte-query";
  import { setMode } from "mode-watcher";
  import { toast } from "svelte-sonner";
  import { on } from "svelte/events";
  import * as Command from "#lib/components/ui/command/index.js";
  import { date } from "#lib/format.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { finder } from "#lib/search.svelte.js";

  const GROUPS = [
    { type: "polity", heading: m.search_group_polity, icon: MapPinIcon },
    { type: "body", heading: m.search_group_body, icon: LandmarkIcon },
    { type: "election", heading: m.search_group_election, icon: VoteIcon },
    { type: "contest", heading: m.search_group_contest, icon: VoteIcon },
    { type: "party", heading: m.search_group_party, icon: FlagIcon },
    { type: "person", heading: m.search_group_person, icon: UserIcon },
  ];
  const STORE = "ea:recent";

  let term = $state("");
  let recent = $state<Array<{ path: string; name: string }>>([]);

  $effect(() => {
    const next = finder.value.trim();
    const timer = setTimeout(() => (term = next), 140);
    return () => clearTimeout(timer);
  });

  $effect(() =>
    on(document, "keydown", (event) => {
      if (
        event.key.toLowerCase() === "k" &&
        (event.metaKey || event.ctrlKey) &&
        !event.altKey
      ) {
        event.preventDefault();
        finder.isOpen = !finder.isOpen;
      }
    }),
  );

  afterNavigate(() => {
    try {
      recent = JSON.parse(localStorage.getItem(STORE) ?? "[]") as Array<{
        path: string;
        name: string;
      }>;
    } catch {
      recent = [];
    }
    const path = page.url.pathname.slice(1);
    if (path === "" || page.status !== 200) {
      return;
    }
    setTimeout(() => {
      const name = document.title.split(" | ")[0]?.trim() ?? "";
      if (name === "") {
        return;
      }
      recent = [
        { path, name },
        ...recent.filter((entry) => entry.path !== path),
      ].slice(0, 6);
      try {
        localStorage.setItem(STORE, JSON.stringify(recent));
      } catch {}
    }, 0);
  });

  const query = createQuery(() => ({
    ...orpc.search.query.queryOptions({ input: { q: term, limit: 20 } }),
    enabled: finder.isOpen && term !== "",
    placeholderData: keepPreviousData,
    meta: { quiet: true },
  }));

  const groups = $derived.by(() => {
    const hits = term === "" ? [] : (query.data ?? []);
    return GROUPS.map((group) => ({
      ...group,
      first: hits.findIndex((hit) => hit.type === group.type),
      hits: hits.filter((hit) => hit.type === group.type && hit.path != null),
    }))
      .filter((group) => group.hits.length > 0)
      .toSorted((a, b) => a.first - b.first);
  });

  const parent = $derived.by(() => {
    if (page.data.kind !== "polity" || !("polity" in page.data)) {
      return null;
    }
    const { polity, id } = page.data as unknown as {
      id: string;
      polity: {
        name: string;
        parent: string | null;
        breadcrumbs: Array<{ name: string }>;
      };
    };
    return polity.parent == null
      ? null
      : {
          id,
          parent: polity.parent,
          label: m.cmd_compare_parent({
            place: polity.name,
            parent: polity.breadcrumbs.at(-1)?.name ?? "",
          }),
        };
  });

  const commands = $derived(
    [
      {
        group: m.search_this_page(),
        items: [
          {
            key: "link",
            label: m.cmd_copy_link(),
            icon: LinkIcon,
            run: () =>
              void navigator.clipboard
                .writeText(page.url.href)
                .then(() => toast.success(m.export_copied())),
          },
          ...(parent == null
            ? []
            : [
                {
                  key: "parent",
                  label: parent.label,
                  icon: ArrowLeftRightIcon,
                  run: () =>
                    void goto(
                      resolve(`/compare?a=${parent.id}&b=${parent.parent}`),
                    ),
                },
              ]),
        ],
      },
      {
        group: m.search_commands(),
        items: [
          {
            key: "home",
            label: m.cmd_home(),
            icon: HouseIcon,
            run: () => void goto(resolve("/")),
          },
          {
            key: "compare",
            label: m.cmd_compare(),
            icon: ArrowLeftRightIcon,
            run: () => void goto(resolve("/compare")),
          },
          {
            key: "workbench",
            label: m.cmd_workbench(),
            icon: BlocksIcon,
            run: () => void goto(resolve("/workbench")),
          },
        ],
      },
      {
        group: m.search_display(),
        items: [
          {
            key: "light",
            label: m.cmd_theme_light(),
            icon: SunIcon,
            run: () => setMode("light"),
          },
          {
            key: "dark",
            label: m.cmd_theme_dark(),
            icon: MoonIcon,
            run: () => setMode("dark"),
          },
          {
            key: "system",
            label: m.cmd_theme_system(),
            icon: MonitorIcon,
            run: () => setMode("system"),
          },
          {
            key: "palette",
            label: palette.isSafe ? m.cmd_palette_off() : m.cmd_palette_on(),
            icon: EyeIcon,
            run: () => (palette.isSafe = !palette.isSafe),
          },
        ],
      },
    ]
      .map((section) => ({
        ...section,
        items: section.items.filter((item) =>
          item.label
            .toLocaleLowerCase()
            .includes(finder.value.trim().toLocaleLowerCase()),
        ),
      }))
      .filter((section) => section.items.length > 0),
  );

  function close() {
    finder.isOpen = false;
    finder.value = "";
  }
</script>

<button
  type="button"
  class="flex items-center gap-2 rounded-sm border border-input bg-card ps-2.5 pe-1.5 text-sm text-muted-foreground block-8 hover:bg-secondary hover:text-foreground"
  onclick={() => (finder.isOpen = true)}
>
  <SearchIcon class="block-4 inline-4" />
  <span class="max-sm:sr-only">{m.search_open()}</span>
  <kbd
    class="rounded-xs border border-input px-1 font-figures text-[0.6875rem] keycap max-sm:hidden"
    >⌘K</kbd
  >
</button>

<Command.Dialog
  bind:open={finder.isOpen}
  title={m.search_title()}
  description={m.search_placeholder()}
  shouldFilter={false}
  class="sm:max-inline-xl"
>
  <Command.Input
    placeholder={m.search_placeholder()}
    bind:value={finder.value}
  />
  <Command.List class="max-block-[min(30rem,60dvh)]">
    {#if finder.value.trim() === ""}
      <p
        class="px-3 pbs-3 pbe-1 text-[0.8125rem] text-pretty text-muted-foreground"
      >
        {m.search_hint()}
      </p>
      {const earlier = $derived(
        recent.filter((entry) => entry.path !== page.url.pathname.slice(1)),
      )}
      {#if earlier.length > 0}
        <Command.Group heading={m.search_recent()}>
          {#each earlier as entry (entry.path)}
            <Command.Item
              value="recent:{entry.path}"
              onSelect={() => {
                close();
                void goto(resolve("/[...path]", { path: entry.path }));
              }}
            >
              <ClockIcon class="text-muted-foreground" />
              <span class="truncate">{entry.name}</span>
            </Command.Item>
          {/each}
        </Command.Group>
      {/if}
    {:else if groups.length === 0 && commands.length === 0}
      <p class="px-3 py-4 text-sm text-muted-foreground">
        {query.isFetching || term !== finder.value.trim()
          ? m.search_loading()
          : m.search_empty({ query: finder.value.trim() })}
      </p>
    {/if}
    {#each groups as group (group.type)}
      <Command.Group heading={group.heading()}>
        {#each group.hits as hit (hit.id)}
          <Command.Item
            value={hit.id}
            class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0 py-1"
            onSelect={() => {
              close();
              if (hit.path != null) {
                void goto(resolve("/[...path]", { path: hit.path }));
              }
            }}
          >
            <group.icon class="row-span-2 text-muted-foreground" />
            <span class="truncate font-semibold">{hit.name}</span>
            {#if hit.context != null || hit.date != null}
              <span class="truncate text-xs text-muted-foreground">
                {[
                  hit.type === "person" && hit.context != null
                    ? m.search_person_latest({ contest: hit.context })
                    : hit.context,
                  hit.date == null ? null : date(hit.date),
                ]
                  .filter((part) => part != null)
                  .join(", ")}
              </span>
            {/if}
          </Command.Item>
        {/each}
      </Command.Group>
    {/each}
    {#each commands as section (section.group)}
      <Command.Group heading={section.group}>
        {#each section.items as item (item.key)}
          <Command.Item
            value="command:{item.key}"
            onSelect={() => {
              close();
              item.run();
            }}
          >
            <item.icon class="text-muted-foreground" />
            {item.label}
          </Command.Item>
        {/each}
      </Command.Group>
    {/each}
  </Command.List>
  <div
    class="flex items-center gap-4 border-bs px-3 text-xs text-muted-foreground block-9 max-sm:hidden"
    aria-hidden="true"
  >
    {#each [["↑↓", m.search_keys_move()], ["↵", m.search_keys_open()], ["esc", m.search_keys_close()]] as [keys, label] (keys)}
      <span class="flex items-center gap-1.5">
        <kbd
          class="rounded-xs border border-input px-1 font-figures text-[0.6875rem] keycap"
          >{keys}</kbd
        >{label}
      </span>
    {/each}
  </div>
</Command.Dialog>
