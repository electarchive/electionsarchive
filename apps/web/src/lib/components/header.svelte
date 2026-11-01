<script lang="ts">
  import { READ_ONLY } from "$app/env/public";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import {
    ArrowLeftRightIcon,
    GaugeIcon,
    LayoutDashboardIcon,
    MenuIcon,
    MonitorIcon,
    MoonIcon,
    SunIcon,
    UserIcon,
  } from "@lucide/svelte";
  import { userPrefersMode } from "mode-watcher";
  import { tick } from "svelte";
  import type HeaderMenus from "#lib/components/header-menus.svelte";
  import Monogram from "#lib/components/monogram.svelte";
  import Search from "#lib/components/search.svelte";
  import Term from "#lib/components/term.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { idle } from "#lib/idle.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const LINKS = [
    {
      href: resolve("/compare"),
      label: m.versus_action(),
      about: m.header_compare_about(),
      icon: ArrowLeftRightIcon,
    },
    ...(READ_ONLY
      ? []
      : [
          {
            href: resolve("/workbench"),
            label: m.workbench_nav(),
            about: m.header_workbench_about(),
            icon: LayoutDashboardIcon,
          },
        ]),
    {
      href: resolve("/coverage"),
      label: m.coverage_nav(),
      about: m.header_coverage_about(),
      icon: GaugeIcon,
    },
  ];

  let Menus = $state<typeof HeaderMenus | null>(null);
  let initial = $state<"theme" | "account" | "menu" | null>(null);
  let loading: Promise<void> | undefined;

  const signIn = $derived(
    `${resolve("/sign-in")}?next=${encodeURIComponent(page.url.pathname)}`,
  );

  function current(href: string) {
    return page.url.pathname === href ||
      page.url.pathname.startsWith(`${href}/`)
      ? "page"
      : undefined;
  }

  function load() {
    loading ??= import("#lib/components/header-menus.svelte").then(
      async (module) => {
        const focused =
          document.activeElement instanceof HTMLElement
            ? document.activeElement.dataset.menu
            : undefined;
        Menus = module.default;
        if (focused != null && initial == null) {
          await tick();
          document
            .querySelector<HTMLElement>(`[data-menu="${focused}"]`)
            ?.focus();
        }
      },
    );
  }

  function placeholder(
    menu: "theme" | "account" | "menu",
    popup: "menu" | "dialog",
  ) {
    return {
      type: "button",
      "aria-haspopup": popup,
      "aria-expanded": "false",
      onclick: () => {
        initial = menu;
        load();
      },
    };
  }

  $effect(() => idle(load));
</script>

{#snippet themeTrigger(props: Record<string, unknown>)}
  <Button {...props} variant="ghost" size="icon" data-menu="theme">
    {#if userPrefersMode.current === "dark"}
      <MoonIcon />
    {:else if userPrefersMode.current === "light"}
      <SunIcon />
    {:else}
      <MonitorIcon />
    {/if}
    <span class="sr-only">{m.theme_menu()}</span>
  </Button>
{/snippet}

{#snippet accountTrigger(props: Record<string, unknown>)}
  <Button {...props} variant="ghost" size="icon" data-menu="account">
    <UserIcon />
    <span class="sr-only">{m.account_menu()}</span>
  </Button>
{/snippet}

{#snippet menuTrigger(props: Record<string, unknown>)}
  <Button
    {...props}
    variant="ghost"
    size="icon"
    class="ms-auto -me-1.5 block-9 inline-9 lg:hidden"
    data-menu="menu"
  >
    <MenuIcon class="block-5 inline-5" />
    <span class="sr-only">{m.header_menu()}</span>
  </Button>
{/snippet}

{#snippet signInButton()}
  <Button href={signIn} variant="ghost">
    {m.sign_in_submit()}
  </Button>
{/snippet}

<header class="border-be">
  <div
    class="mx-auto flex items-center gap-3 px-4 block-14 max-inline-7xl lg:gap-4 max-sm:gap-2 max-sm:px-3"
  >
    <div class="relative shrink-0">
      <a
        href={resolve("/")}
        class="flex items-center gap-2.5 max-[24rem]:gap-2"
      >
        <Monogram size={26} />
        <span
          class="text-lg font-[780] tracking-[-0.015em] whitespace-nowrap max-[24rem]:text-base max-[22.5rem]:sr-only"
          >{m.wordmark()}</span
        >
      </a>
      <Term
        class="absolute -inset-e-3 inset-bs-[calc(100%-0.375rem)] -rotate-3 rounded-[2px] bg-marker px-1 text-[0.625rem]/[0.9rem] font-[780] tracking-[0.02em] text-[#15171a] no-underline transition-transform hover:rotate-0 focus-visible:outline-2 focus-visible:outline-ring motion-reduce:transition-none max-sm:-inset-e-0.5"
        label={m.beta_label()}
        definition={m.beta_definition()}
        side="bottom"
        align="start"
      />
    </div>
    <Search
      class={cn("flex-1 lg:max-inline-md", {
        hidden: page.url.pathname === "/",
      })}
    />
    <nav
      class="ms-auto flex items-center gap-1 max-lg:hidden"
      aria-label={m.header_menu()}
    >
      {#each LINKS as link (link.href)}
        <Button
          href={link.href}
          variant="ghost"
          class="aria-[current=page]:highlighter"
          aria-current={current(link.href)}
        >
          <link.icon />
          {link.label}
        </Button>
      {/each}
    </nav>
    {#if Menus != null}
      <Menus
        links={LINKS}
        {current}
        {signIn}
        {initial}
        {themeTrigger}
        {accountTrigger}
        {menuTrigger}
        {signInButton}
      />
    {:else}
      <div class="flex items-center gap-1 max-lg:hidden">
        {@render themeTrigger(placeholder("theme", "menu"))}
        {#if page.data.user == null}
          {@render signInButton()}
        {:else}
          {@render accountTrigger(placeholder("account", "menu"))}
        {/if}
      </div>
      {@render menuTrigger(placeholder("menu", "dialog"))}
    {/if}
  </div>
</header>
