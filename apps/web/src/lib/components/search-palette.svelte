<script lang="ts">
  import type { InferRouterOutputs } from "@orpc/server";
  import type { Component } from "svelte";
  import { READ_ONLY } from "$app/env/public";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import BlocksIcon from "@lucide/svelte/icons/blocks";
  import CalendarDaysIcon from "@lucide/svelte/icons/calendar-days";
  import ClockIcon from "@lucide/svelte/icons/clock";
  import CornerDownLeftIcon from "@lucide/svelte/icons/corner-down-left";
  import EyeIcon from "@lucide/svelte/icons/eye";
  import FlagIcon from "@lucide/svelte/icons/flag";
  import HouseIcon from "@lucide/svelte/icons/house";
  import LandmarkIcon from "@lucide/svelte/icons/landmark";
  import LinkIcon from "@lucide/svelte/icons/link";
  import MapPinIcon from "@lucide/svelte/icons/map-pin";
  import MonitorIcon from "@lucide/svelte/icons/monitor";
  import MoonIcon from "@lucide/svelte/icons/moon";
  import PencilIcon from "@lucide/svelte/icons/pencil";
  import SearchIcon from "@lucide/svelte/icons/search";
  import SquarePenIcon from "@lucide/svelte/icons/square-pen";
  import SunIcon from "@lucide/svelte/icons/sun";
  import UserIcon from "@lucide/svelte/icons/user";
  import VoteIcon from "@lucide/svelte/icons/vote";
  import { createQuery } from "@tanstack/svelte-query";
  import { Dialog as DialogPrimitive } from "bits-ui";
  import { setMode } from "mode-watcher";
  import { toast } from "svelte-sonner";
  import type { Router } from "#lib/server/rpc/router.js";
  import { copy } from "#lib/clipboard.js";
  import Logo from "#lib/components/logo.svelte";
  import * as Dialog from "#lib/components/ui/dialog/index.js";
  import { date } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { editable, finder } from "#lib/search.svelte.js";
  import { cn } from "#lib/utils.js";

  type Hit = InferRouterOutputs<Router>["search"]["query"][number];
  type Scope =
    | "all"
    | "polity"
    | "body"
    | "election"
    | "contest"
    | "party"
    | "person";
  type Item =
    | { kind: "hit"; key: string; hit: Hit; href: string }
    | {
        kind: "link";
        key: string;
        label: string;
        href: string;
        icon: Component;
      }
    | {
        kind: "command";
        key: string;
        label: string;
        icon: Component;
        run: () => void;
      }
    | { kind: "example"; key: string; label: string };
  type Section = {
    key: string;
    heading: string | null;
    items: Array<Item>;
    isStale: boolean;
    isLead?: boolean;
  };

  const SCOPES: Array<{ value: Scope; label: () => string }> = [
    { value: "all", label: m.search_scope_all },
    { value: "polity", label: m.search_group_polity },
    { value: "body", label: m.search_group_body },
    { value: "election", label: m.search_group_election },
    { value: "contest", label: m.search_group_contest },
    { value: "party", label: m.search_group_party },
    { value: "person", label: m.search_group_person },
  ];
  const ICONS: Record<string, Component> = {
    polity: MapPinIcon,
    body: LandmarkIcon,
    election: CalendarDaysIcon,
    contest: VoteIcon,
    party: FlagIcon,
    person: UserIcon,
  };
  const EXAMPLES = [
    "Sherrod Brown",
    "Georgia 2022",
    "Bundestag",
    "NY-14",
    "Labour",
  ];
  const FOLDS: Record<string, string> = {
    ø: "o",
    ł: "l",
    ı: "i",
    đ: "d",
    ß: "ss",
    æ: "ae",
    œ: "oe",
    þ: "th",
  };

  const uid = $props.id();
  let input = $state<HTMLInputElement | null>(null);
  let scope = $state<Scope>("all");
  let term = $state("");
  let isPending = $state(false);
  let last = $state.raw<Array<Hit>>([]);

  const raw = $derived(finder.value.trimStart());
  const isCommandsOnly = $derived(raw.startsWith(">"));
  const text = $derived((isCommandsOnly ? raw.slice(1) : raw).trim());
  const wanted = $derived(isCommandsOnly ? "" : text);
  const isSearchable = $derived([...wanted].length >= 2);
  const types = $derived(scope === "all" ? undefined : [scope]);

  $effect(() => {
    const next = wanted;
    if (next === "") {
      term = "";
      return;
    }
    const timer = setTimeout(() => (term = next), 120);
    return () => clearTimeout(timer);
  });

  $effect(() => {
    if (!isSearchable) {
      last = [];
    }
  });

  const recent = $derived.by(() => {
    if (!finder.isOpen) {
      return [];
    }
    try {
      return JSON.parse(localStorage.getItem("ea:recent") ?? "[]") as Array<{
        path: string;
        name: string;
      }>;
    } catch {
      return [];
    }
  });

  const query = createQuery(() => ({
    ...orpc.search.query.queryOptions({
      input: { q: term, limit: 20, ...(types == null ? {} : { types }) },
    }),
    enabled: finder.isOpen && [...term].length >= 2,
    staleTime: 5 * 60_000,
    meta: { quiet: true },
  }));

  const isFresh = $derived(
    isSearchable && term === wanted && query.data != null,
  );

  $effect(() => {
    if (query.data != null) {
      last = query.data;
    }
  });

  const hits = $derived(
    isFresh ? (query.data ?? []) : isSearchable ? last : [],
  );
  const isStale = $derived(isSearchable && !isFresh && hits.length > 0);
  const isBusy = $derived(
    isSearchable && !isFresh && !(term === wanted && query.isError),
  );
  const isFailed = $derived(isSearchable && term === wanted && query.isError);

  const words = $derived(
    fold(wanted)
      .split(/[\s,]+/u)
      .filter((word) => word !== ""),
  );

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

  const commands = $derived.by(() => {
    const sections: Array<{
      key: string;
      heading: string;
      items: Array<Item>;
    }> = [
      {
        key: "page",
        heading: m.search_this_page(),
        items: [
          {
            kind: "command",
            key: "link",
            label: m.cmd_copy_link(),
            icon: LinkIcon,
            run: () =>
              void copy((page.shallow?.url ?? page.url).href).then(() =>
                toast.success(m.export_copied()),
              ),
          },
          ...(editable.entity == null
            ? []
            : [
                {
                  kind: "command" as const,
                  key: "edit",
                  label: m.cmd_edit(),
                  icon: PencilIcon,
                  run: () => keep({ edit: "" }),
                },
                ...(page.data.user == null
                  ? []
                  : [
                      {
                        kind: "command" as const,
                        key: "workspace",
                        label: m.ws_open(),
                        icon: SquarePenIcon,
                        run: () =>
                          void goto(resolve(`/edit?record=${editable.entity}`)),
                      },
                    ]),
              ]),
          ...(parent == null
            ? []
            : [
                {
                  kind: "command" as const,
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
        key: "go",
        heading: m.search_commands(),
        items: [
          {
            kind: "command",
            key: "home",
            label: m.cmd_home(),
            icon: HouseIcon,
            run: () => void goto(resolve("/")),
          },
          {
            kind: "command",
            key: "compare",
            label: m.cmd_compare(),
            icon: ArrowLeftRightIcon,
            run: () => void goto(resolve("/compare")),
          },
          ...(READ_ONLY
            ? []
            : [
                {
                  kind: "command" as const,
                  key: "workbench",
                  label: m.cmd_workbench(),
                  icon: BlocksIcon,
                  run: () => void goto(resolve("/workbench")),
                },
              ]),
        ],
      },
      {
        key: "display",
        heading: m.search_display(),
        items: [
          {
            kind: "command",
            key: "light",
            label: m.cmd_theme_light(),
            icon: SunIcon,
            run: () => setMode("light"),
          },
          {
            kind: "command",
            key: "dark",
            label: m.cmd_theme_dark(),
            icon: MoonIcon,
            run: () => setMode("dark"),
          },
          {
            kind: "command",
            key: "system",
            label: m.cmd_theme_system(),
            icon: MonitorIcon,
            run: () => setMode("system"),
          },
          {
            kind: "command",
            key: "palette",
            label: palette.isSafe ? m.cmd_palette_off() : m.cmd_palette_on(),
            icon: EyeIcon,
            run: () => (palette.isSafe = !palette.isSafe),
          },
        ],
      },
    ];
    const needle = fold(text);
    const matched: Array<Section> = [];
    for (const section of sections) {
      const items = section.items.filter(
        (item) => item.kind === "command" && fold(item.label).includes(needle),
      );
      if (items.length > 0) {
        matched.push({ ...section, items, isStale: false });
      }
    }
    return matched;
  });

  const sections = $derived.by(() => {
    const out: Array<Section> = [];
    const href = (path: string) => resolve("/[...path]", { path });
    if (isCommandsOnly) {
      return commands;
    }
    if (text === "") {
      out.push({
        key: "examples",
        heading: m.search_examples(),
        items: EXAMPLES.map((label) => ({
          kind: "example",
          key: `example:${label}`,
          label,
        })),
        isStale: false,
      });
      const earlier = recent.filter(
        (entry) => entry.path !== page.url.pathname.slice(1),
      );
      if (earlier.length > 0) {
        out.push({
          key: "recent",
          heading: m.search_recent(),
          items: earlier.map((entry) => ({
            kind: "link",
            key: `recent:${entry.path}`,
            label: entry.name,
            href: href(entry.path),
            icon: ClockIcon,
          })),
          isStale: false,
        });
      }
      return [...out, ...commands];
    }
    const located = hits.filter((hit) => hit.path != null);
    const lead = located.find((hit) => hit.isBest);
    if (lead != null) {
      out.push({
        key: "lead",
        heading: m.search_best(),
        items: [toItem(lead)],
        isStale,
        isLead: true,
      });
    }
    const similar = located.some((hit) => !hit.isSimilar)
      ? located.filter((hit) => hit.isSimilar && hit !== lead)
      : [];
    const rest = located.filter(
      (hit) => hit !== lead && !similar.includes(hit),
    );
    for (const [type, group] of Map.groupBy(rest, (hit) => hit.type)) {
      out.push({
        key: `type:${type}`,
        heading: SCOPES.find((entry) => entry.value === type)?.label() ?? null,
        items: group.map(toItem),
        isStale,
      });
    }
    if (similar.length > 0) {
      out.push({
        key: "similar",
        heading: m.search_similar(),
        items: similar.map(toItem),
        isStale,
      });
    }
    if (isFresh) {
      const more: Array<Item> = [];
      if (scope !== "all") {
        more.push({
          kind: "command",
          key: "widen",
          label: m.search_widen({ query: wanted }),
          icon: SearchIcon,
          run: () => (scope = "all"),
        });
      }
      if (located.length > 0) {
        more.push({
          kind: "link",
          key: "all",
          label: m.search_all({ query: wanted }),
          href: resolve(
            `/search?${new URLSearchParams({ q: wanted }).toString()}`,
          ),
          icon: SearchIcon,
        });
      }
      if (more.length > 0) {
        out.push({ key: "more", heading: null, items: more, isStale: false });
      }
    }
    return [...out, ...commands];
  });

  const flat = $derived(sections.flatMap((section) => section.items));
  let active = $derived.by(() => {
    void wanted;
    void scope;
    void isFresh;
    return 0;
  });
  const count = $derived(
    isFresh ? hits.filter((hit) => hit.path != null).length : 0,
  );
  const isMac = /Mac|iPhone|iPad/u.test(navigator.userAgent);
  const target = $derived(flat[Math.min(active, flat.length - 1)]);

  $effect(() => {
    if (isPending && isFresh) {
      isPending = false;
      const first = flat.find((item) => item.kind === "hit");
      if (first != null) {
        run(first, false);
      }
    }
  });

  $effect(() => {
    if (target != null) {
      document
        .getElementById(`${uid}-${target.key}`)
        ?.scrollIntoView({ block: "nearest" });
    }
  });

  function toItem(hit: Hit): Item {
    return {
      kind: "hit",
      key: `hit:${hit.id}`,
      hit,
      href: resolve("/[...path]", { path: hit.path ?? "" }),
    };
  }

  function fold(value: string) {
    let out = "";
    for (const char of value.toLowerCase().normalize("NFD")) {
      if (!/\p{M}/u.test(char)) {
        out += FOLDS[char] ?? char;
      }
    }
    return out;
  }

  function segments(name: string) {
    let folded = "";
    const from: Array<number> = [];
    const to: Array<number> = [];
    let offset = 0;
    for (const char of name) {
      const piece = fold(char);
      for (let index = 0; index < piece.length; index++) {
        from.push(offset);
        to.push(offset + char.length);
      }
      folded += piece;
      offset += char.length;
    }
    const lit = new Array<boolean>(name.length).fill(false);
    for (const word of words) {
      let at = folded.indexOf(word);
      while (at !== -1) {
        if (at === 0 || !/[\p{L}\p{N}]/u.test(folded[at - 1] ?? "")) {
          for (
            let index = from[at] ?? 0;
            index < (to[at + word.length - 1] ?? 0);
            index++
          ) {
            lit[index] = true;
          }
        }
        at = folded.indexOf(word, at + 1);
      }
    }
    const out: Array<{ text: string; isLit: boolean }> = [];
    let start = 0;
    for (let index = 1; index <= name.length; index++) {
      if (index === name.length || lit[index] !== lit[start]) {
        out.push({
          text: name.slice(start, index),
          isLit: lit[start] ?? false,
        });
        start = index;
      }
    }
    return out;
  }

  function close() {
    finder.isOpen = false;
  }

  function reset() {
    finder.value = "";
    scope = "all";
    term = "";
    last = [];
    isPending = false;
  }

  function run(item: Item, isNewTab: boolean) {
    if (item.kind === "example") {
      finder.value = item.label;
      input?.focus();
      return;
    }
    if (item.kind === "command") {
      if (item.key === "widen") {
        item.run();
        return;
      }
      close();
      item.run();
      return;
    }
    if (isNewTab) {
      window.open(item.href, "_blank", "noopener");
      return;
    }
    close();
    void goto(item.href);
  }

  function move(by: number, isWrapping: boolean) {
    if (flat.length === 0) {
      return;
    }
    const next = active + by;
    active = isWrapping
      ? (next + flat.length) % flat.length
      : Math.max(0, Math.min(flat.length - 1, next));
  }

  function onkeydown(event: KeyboardEvent) {
    if (event.isComposing) {
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(event.key === "ArrowDown" ? 1 : -1, true);
    } else if (event.key === "PageDown" || event.key === "PageUp") {
      event.preventDefault();
      move(event.key === "PageDown" ? 8 : -8, false);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (target == null) {
        if (isBusy) {
          isPending = true;
        }
        return;
      }
      if (target.kind === "hit" && isStale) {
        isPending = true;
        return;
      }
      run(target, event.metaKey || event.ctrlKey);
    } else if (event.key === "Tab" && !isCommandsOnly) {
      event.preventDefault();
      const index = SCOPES.findIndex((entry) => entry.value === scope);
      const next =
        SCOPES[
          (index + (event.shiftKey ? -1 : 1) + SCOPES.length) % SCOPES.length
        ];
      scope = next?.value ?? "all";
    }
  }
</script>

{#snippet glyph(hit: Hit, size: number)}
  {#if hit.type === "party" && hit.logo?.shape === "mark"}
    <Logo logo={hit.logo} label={hit.name} kind="mark" {size} />
  {:else if hit.type === "party"}
    <span
      class="grid place-items-center"
      style:inline-size="{size}px"
      style:block-size="{size}px"
      aria-hidden="true"
    >
      <span
        class="rounded-full chip block-2.5 inline-2.5"
        style:background-color={swatch(hit.color, `party:${hit.id}`)}
      ></span>
    </span>
  {:else if hit.type === "person" && hit.photo != null}
    <img
      class="rounded-sm bg-muted object-cover object-top"
      src={hit.photo}
      alt=""
      loading="lazy"
      width={size}
      height={size}
      style:inline-size="{size}px"
      style:block-size="{size}px"
    />
  {:else}
    {const Icon = $derived(ICONS[hit.type] ?? SearchIcon)}
    <span
      class="grid place-items-center text-muted-foreground"
      style:inline-size="{size}px"
      style:block-size="{size}px"
    >
      <Icon class="block-4 inline-4" />
    </span>
  {/if}
{/snippet}

{#snippet name(value: string)}
  {#each segments(value) as part, index (index)}
    {#if part.isLit}
      <b class="font-bold text-foreground">{part.text}</b>
    {:else}
      {part.text}
    {/if}
  {/each}
{/snippet}

<Dialog.Root
  bind:open={finder.isOpen}
  onOpenChangeComplete={(open) => {
    if (open === false) {
      reset();
    }
  }}
>
  <Dialog.Portal>
    <Dialog.Overlay />
    <DialogPrimitive.Content
      class="fixed inset-s-1/2 inset-bs-[clamp(0.5rem,12dvh,7rem)] menu z-50 flex -translate-x-1/2 flex-col overflow-hidden rounded-lg inline-[min(44rem,calc(100%-1rem))] max-block-[calc(100dvh-clamp(0.5rem,12dvh,7rem)-1rem)]"
      onOpenAutoFocus={(event) => {
        event.preventDefault();
        input?.focus();
      }}
    >
      <DialogPrimitive.Title class="sr-only"
        >{m.search_title()}</DialogPrimitive.Title
      >
      <DialogPrimitive.Description class="sr-only"
        >{m.search_placeholder()}</DialogPrimitive.Description
      >
      <div
        class="relative flex shrink-0 items-center gap-3 border-be ps-4 pe-3 block-14"
      >
        <SearchIcon
          class="shrink-0 text-muted-foreground block-4.5 inline-4.5"
        />
        <input
          bind:this={input}
          bind:value={finder.value}
          class="flex-1 bg-transparent text-[1.0625rem] font-medium outline-none min-inline-0 placeholder:font-normal placeholder:text-muted-foreground"
          type="text"
          role="combobox"
          autocomplete="off"
          autocapitalize="off"
          spellcheck="false"
          enterkeyhint="go"
          placeholder={m.search_placeholder()}
          aria-label={m.search_title()}
          aria-expanded="true"
          aria-controls="{uid}-list"
          aria-autocomplete="list"
          aria-activedescendant={target == null
            ? undefined
            : `${uid}-${target.key}`}
          {onkeydown}
        />
        <kbd class="max-sm:hidden">esc</kbd>
        {#if isBusy}
          <span class="sweep" aria-hidden="true"></span>
        {/if}
      </div>
      {#if !isCommandsOnly}
        <div
          class="flex shrink-0 scrollbar-none gap-1 overflow-x-auto border-be px-2.5"
          role="toolbar"
          aria-label={m.search_scope_label()}
        >
          {#each SCOPES as entry (entry.value)}
            <button
              type="button"
              tabindex="-1"
              class={cn(
                "shrink-0 px-1.5 text-[0.8125rem] text-muted-foreground block-9 hover:text-foreground",
                {
                  "marked font-semibold text-foreground": scope === entry.value,
                },
              )}
              aria-pressed={scope === entry.value}
              onclick={() => {
                scope = entry.value;
                input?.focus();
              }}>{entry.label()}</button
            >
          {/each}
        </div>
      {/if}
      <div
        id="{uid}-list"
        class="flex-1 overflow-y-auto overscroll-contain pbe-1.5 min-block-0"
        role="listbox"
        aria-label={m.search_title()}
        aria-busy={isBusy}
      >
        {#if isFailed}
          <div class="flex items-center justify-between gap-4 p-4 text-sm">
            <p class="text-pretty text-muted-foreground">{m.search_error()}</p>
            <button
              type="button"
              class="shrink-0 rounded-sm border px-2.5 font-medium block-8 hover:bg-accent"
              onclick={() => {
                void query.refetch();
                input?.focus();
              }}>{m.search_retry()}</button
            >
          </div>
        {:else if text !== "" && !isCommandsOnly && !isSearchable}
          <p class="px-4 pbs-3.5 pbe-1 text-[0.8125rem] text-muted-foreground">
            {m.search_short()}
          </p>
        {:else if isSearchable && hits.length === 0 && isBusy}
          <p class="px-4 pbs-3.5 pbe-1 text-[0.8125rem] text-muted-foreground">
            {m.search_loading()}
          </p>
        {:else if isFresh && count === 0}
          <p
            class="px-4 pbs-3.5 pbe-1 text-[0.8125rem] text-pretty text-muted-foreground"
          >
            {m.search_empty({ query: wanted })}
          </p>
        {/if}
        {#each sections as section (section.key)}
          <div
            role="group"
            aria-labelledby={section.heading == null
              ? undefined
              : `${uid}-${section.key}-heading`}
            class={cn("pbs-1.5 transition-opacity duration-150", {
              "opacity-45 delay-150": section.isStale,
            })}
            inert={section.isStale}
          >
            {#if section.heading != null}
              <div
                id="{uid}-{section.key}-heading"
                class="px-4 pbs-2 pbe-1 text-xs font-semibold text-muted-foreground"
                role="presentation"
              >
                {section.heading}
              </div>
            {/if}
            <div
              class={cn({
                "flex flex-wrap gap-1.5 px-4 pbs-0.5 pbe-1":
                  section.key === "examples",
              })}
            >
              {#each section.items as item (item.key)}
                {const index = $derived(flat.indexOf(item))}
                {const isActive = $derived(index === active)}
                {#if item.kind === "hit" && section.isLead}
                  <a
                    id="{uid}-{item.key}"
                    href={item.href}
                    role="option"
                    aria-selected={isActive}
                    tabindex="-1"
                    class={cn(
                      "relative mx-1.5 flex items-center gap-3 rounded-sm px-2.5 text-sm outline-none",
                      {
                        "highlighter shadow-[inset_2px_0_0_var(--foreground)]":
                          isActive,
                      },
                      "items-start gap-4 py-3",
                    )}
                    onpointermove={() => (active = index)}
                    onclick={(event) => {
                      if (!event.metaKey && !event.ctrlKey && !event.shiftKey) {
                        close();
                      }
                    }}
                  >
                    {@render glyph(item.hit, 56)}
                    <span class="grid gap-0.5 min-inline-0">
                      <span class="heading text-pretty"
                        >{@render name(item.hit.name)}</span
                      >
                      {#if item.hit.office != null}
                        <span class="font-medium">{item.hit.office}</span>
                      {/if}
                      {#if item.hit.ballot != null}
                        <span class="font-figures text-[0.8125rem]">
                          {m.search_ballot({
                            date: date(item.hit.ballot.date),
                            office: item.hit.ballot.label,
                          })}
                        </span>
                      {:else if item.hit.context != null}
                        <span
                          class="truncate text-[0.8125rem] text-muted-foreground"
                        >
                          {m.search_person_latest({
                            contest: item.hit.context,
                          })}
                        </span>
                      {/if}
                    </span>
                  </a>
                {:else if item.kind === "hit"}
                  <a
                    id="{uid}-{item.key}"
                    href={item.href}
                    role="option"
                    aria-selected={isActive}
                    tabindex="-1"
                    class={cn(
                      "relative mx-1.5 flex items-center gap-3 rounded-sm px-2.5 text-sm outline-none",
                      {
                        "highlighter shadow-[inset_2px_0_0_var(--foreground)]":
                          isActive,
                      },
                      "py-1.5",
                    )}
                    onpointermove={() => (active = index)}
                    onclick={(event) => {
                      if (!event.metaKey && !event.ctrlKey && !event.shiftKey) {
                        close();
                      }
                    }}
                  >
                    {@render glyph(item.hit, 28)}
                    <span class="grid flex-1 min-inline-0">
                      <span class="truncate font-medium"
                        >{@render name(item.hit.name)}</span
                      >
                      {#if item.hit.context != null}
                        <span
                          class="truncate text-[0.8125rem] text-muted-foreground"
                        >
                          {item.hit.type === "person" &&
                          item.hit.context !== item.hit.office
                            ? m.search_person_latest({
                                contest: item.hit.context,
                              })
                            : item.hit.context}
                        </span>
                      {/if}
                    </span>
                    {#if item.hit.date != null}
                      <span
                        class="shrink-0 font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                      >
                        {date(item.hit.date)}
                      </span>
                    {/if}
                  </a>
                {:else if item.kind === "link"}
                  <a
                    id="{uid}-{item.key}"
                    href={item.href}
                    role="option"
                    aria-selected={isActive}
                    tabindex="-1"
                    class={cn(
                      "relative mx-1.5 flex items-center gap-3 rounded-sm px-2.5 text-sm outline-none",
                      {
                        "highlighter shadow-[inset_2px_0_0_var(--foreground)]":
                          isActive,
                      },
                      "block-9",
                    )}
                    onpointermove={() => (active = index)}
                    onclick={(event) => {
                      if (!event.metaKey && !event.ctrlKey && !event.shiftKey) {
                        close();
                      }
                    }}
                  >
                    <item.icon
                      class="shrink-0 text-muted-foreground block-4 inline-4"
                    />
                    <span class="truncate">{item.label}</span>
                  </a>
                {:else if item.kind === "example"}
                  <button
                    id="{uid}-{item.key}"
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    tabindex="-1"
                    class={cn(
                      "rounded-sm border px-2.5 text-[0.8125rem] outline-none block-7 hover:bg-accent",
                      { "border-foreground highlighter": isActive },
                    )}
                    onpointermove={() => (active = index)}
                    onclick={() => run(item, false)}>{item.label}</button
                  >
                {:else}
                  <button
                    id="{uid}-{item.key}"
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    tabindex="-1"
                    class={cn(
                      "relative mx-1.5 flex items-center gap-3 rounded-sm px-2.5 text-sm outline-none",
                      {
                        "highlighter shadow-[inset_2px_0_0_var(--foreground)]":
                          isActive,
                      },
                      "text-start block-9 inline-[calc(100%-0.75rem)]",
                    )}
                    onpointermove={() => (active = index)}
                    onclick={() => run(item, false)}
                  >
                    <item.icon
                      class="shrink-0 text-muted-foreground block-4 inline-4"
                    />
                    <span class="truncate">{item.label}</span>
                  </button>
                {/if}
              {/each}
            </div>
          </div>
        {/each}
      </div>
      <p class="sr-only" aria-live="polite">
        {isFresh
          ? count > 0
            ? m.search_count({ count })
            : m.search_empty({ query: wanted })
          : ""}
      </p>
      <div
        class="flex shrink-0 items-center gap-4 border-bs px-4 text-xs text-muted-foreground block-10 max-sm:hidden"
        aria-hidden="true"
      >
        <span class="flex items-center gap-1.5"
          ><kbd>↑</kbd><kbd>↓</kbd>{m.search_keys_move()}</span
        >
        <span class="flex items-center gap-1.5"
          ><kbd><CornerDownLeftIcon class="block-3 inline-3" /></kbd
          >{m.search_keys_open()}</span
        >
        <span class="flex items-center gap-1.5"
          ><kbd>{isMac ? "⌘↵" : "Ctrl ↵"}</kbd>{m.search_keys_tab()}</span
        >
        {#if !isCommandsOnly}
          <span class="flex items-center gap-1.5"
            ><kbd>tab</kbd>{m.search_keys_scope()}</span
          >
        {/if}
        <span class="flex items-center gap-1.5"
          ><kbd>&gt;</kbd>{m.search_keys_commands()}</span
        >
        {#if count > 0}
          <span class="ms-auto font-figures tabular-nums"
            >{m.search_count({ count })}</span
          >
        {/if}
      </div>
    </DialogPrimitive.Content>
  </Dialog.Portal>
</Dialog.Root>

<style>
  .sweep {
    position: absolute;
    inset-inline: 0;
    inset-block-end: -1px;
    block-size: 2px;
    overflow: hidden;
    opacity: 0;
    animation: reveal 120ms ease-out 180ms forwards;
  }

  .sweep::before {
    content: "";
    position: absolute;
    inset-block: 0;
    inline-size: 32%;
    background-color: var(--marker);
    animation: sweep 1.1s cubic-bezier(0.4, 0, 0.2, 1) infinite;
  }

  @keyframes reveal {
    to {
      opacity: 1;
    }
  }

  @keyframes sweep {
    from {
      translate: -100% 0;
    }
    to {
      translate: 320% 0;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .sweep::before {
      inline-size: 100%;
      animation: none;
    }
  }
</style>
