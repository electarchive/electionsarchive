<script lang="ts">
  import { READ_ONLY } from "$app/env/public";
  import { afterNavigate } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import {
    ArrowLeftRightIcon,
    AtSignIcon,
    ChevronRightIcon,
    GaugeIcon,
    KeyRoundIcon,
    LayoutDashboardIcon,
    LogInIcon,
    LogOutIcon,
    MenuIcon,
    MonitorIcon,
    MoonIcon,
    SunIcon,
    UserIcon,
  } from "@lucide/svelte";
  import { setMode, userPrefersMode } from "mode-watcher";
  import Monogram from "#lib/components/monogram.svelte";
  import Search from "#lib/components/search.svelte";
  import Term from "#lib/components/term.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { Switch } from "#lib/components/ui/switch/index.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const THEMES = [
    { value: "system", label: m.theme_system(), icon: MonitorIcon },
    { value: "light", label: m.theme_light(), icon: SunIcon },
    { value: "dark", label: m.theme_dark(), icon: MoonIcon },
  ] as const;

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

  let isMenuOpen = $state(false);

  const signIn = $derived(
    `${resolve("/sign-in")}?next=${encodeURIComponent(page.url.pathname)}`,
  );

  function current(href: string) {
    return page.url.pathname === href ||
      page.url.pathname.startsWith(`${href}/`)
      ? "page"
      : undefined;
  }

  function theme(value: unknown) {
    const found = THEMES.find((option) => option.value === value);
    if (found != null) {
      setMode(found.value);
    }
  }

  afterNavigate(() => {
    isMenuOpen = false;
  });
</script>

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
    <div class="flex items-center gap-1 max-lg:hidden">
      <DropdownMenu.Root>
        <DropdownMenu.Trigger>
          {#snippet child({ props })}
            <Button {...props} variant="ghost" size="icon">
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
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="end" class="inline-56">
          <DropdownMenu.RadioGroup
            value={userPrefersMode.current}
            onValueChange={theme}
          >
            <DropdownMenu.GroupHeading
              >{m.theme_menu()}</DropdownMenu.GroupHeading
            >
            {#each THEMES as theme (theme.value)}
              <DropdownMenu.RadioItem value={theme.value}>
                <theme.icon class="text-muted-foreground" />
                {theme.label}
              </DropdownMenu.RadioItem>
            {/each}
          </DropdownMenu.RadioGroup>
          <DropdownMenu.Separator />
          <DropdownMenu.CheckboxItem bind:checked={palette.isSafe}>
            {m.palette_safe()}
          </DropdownMenu.CheckboxItem>
        </DropdownMenu.Content>
      </DropdownMenu.Root>
      {#if page.data.user == null}
        <Button href={signIn} variant="ghost">
          {m.sign_in_submit()}
        </Button>
      {:else}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger>
            {#snippet child({ props })}
              <Button {...props} variant="ghost" size="icon">
                <UserIcon />
                <span class="sr-only">{m.account_menu()}</span>
              </Button>
            {/snippet}
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="end" class="inline-56">
            <DropdownMenu.Label class="grid gap-0.5">
              <span class="truncate text-foreground">{page.data.user.name}</span
              >
              <span class="truncate font-normal">{page.data.user.email}</span>
            </DropdownMenu.Label>
            <DropdownMenu.Separator />
            <DropdownMenu.Item>
              {#snippet child({ props })}
                <a {...props} href={resolve("/settings/username")}>
                  <AtSignIcon class="text-muted-foreground" />
                  {m.username_nav()}
                </a>
              {/snippet}
            </DropdownMenu.Item>
            {#if !READ_ONLY}
              <DropdownMenu.Item>
                {#snippet child({ props })}
                  <a {...props} href={resolve("/settings/keys")}>
                    <KeyRoundIcon class="text-muted-foreground" />
                    {m.keys_nav()}
                  </a>
                {/snippet}
              </DropdownMenu.Item>
            {/if}
            <form method="POST" action={resolve("/sign-out")}>
              <DropdownMenu.Item class="inline-full">
                {#snippet child({ props })}
                  <button {...props} type="submit">
                    <LogOutIcon class="text-muted-foreground" />
                    {m.sign_out()}
                  </button>
                {/snippet}
              </DropdownMenu.Item>
            </form>
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      {/if}
    </div>
    <Drawer.Root bind:open={isMenuOpen} shouldScaleBackground={false}>
      <Drawer.Trigger>
        {#snippet child({ props })}
          <Button
            {...props}
            variant="ghost"
            size="icon"
            class="ms-auto -me-1.5 block-9 inline-9 lg:hidden"
          >
            <MenuIcon class="block-5 inline-5" />
            <span class="sr-only">{m.header_menu()}</span>
          </Button>
        {/snippet}
      </Drawer.Trigger>
      <Drawer.Content class="sm:mx-auto sm:max-inline-xl">
        <Drawer.Title class="sr-only">{m.header_menu()}</Drawer.Title>
        <div
          class="grid gap-4 px-3 pbs-3 pbe-[calc(env(safe-area-inset-bottom)+1rem)]"
        >
          <nav class="grid" aria-label={m.header_menu()}>
            {#each LINKS as link (link.href)}
              <a
                href={link.href}
                class="group/link flex items-center gap-3 rounded-sm px-2 py-2.5 hover:highlighter aria-[current=page]:highlighter"
                aria-current={current(link.href)}
              >
                <span
                  class="grid shrink-0 place-items-center rounded-sm border border-input bg-card block-10 inline-10 group-aria-[current=page]/link:border-foreground"
                >
                  <link.icon class="block-4.5 inline-4.5" />
                </span>
                <span class="grid flex-1 gap-0.5 min-inline-0">
                  <span class="text-[0.9375rem]/tight font-semibold"
                    >{link.label}</span
                  >
                  <span
                    class="text-[0.8125rem]/snug text-pretty text-muted-foreground"
                    >{link.about}</span
                  >
                </span>
                <ChevronRightIcon
                  class="shrink-0 text-muted-foreground block-4 inline-4"
                />
              </a>
            {/each}
          </nav>
          <div class="grid gap-3 border-bs px-2 pbs-4">
            <div class="flex items-center justify-between gap-3">
              <span class="text-sm font-semibold">{m.theme_menu()}</span>
              <Segmented
                options={[...THEMES]}
                value={userPrefersMode.current}
                label={m.theme_menu()}
                size="sm"
                onValueChange={theme}
              />
            </div>
            <label class="flex items-center justify-between gap-3 text-sm">
              {m.palette_safe()}
              <Switch bind:checked={palette.isSafe} />
            </label>
          </div>
          <div class="grid gap-3 border-bs px-2 pbs-4">
            {#if page.data.user == null}
              <p
                class="text-[0.8125rem]/snug text-pretty text-muted-foreground"
              >
                {m.header_sign_in_about()}
              </p>
              <Button href={signIn} class="block-10">
                <LogInIcon />
                {m.sign_in_submit()}
              </Button>
            {:else}
              <div class="flex items-center gap-3">
                <span
                  class="grid shrink-0 place-items-center rounded-full border border-input bg-card block-9 inline-9"
                >
                  <UserIcon class="block-4 inline-4" />
                </span>
                <span class="grid text-sm min-inline-0">
                  <span class="truncate font-semibold"
                    >{page.data.user.name}</span
                  >
                  <span class="truncate text-muted-foreground"
                    >{page.data.user.email}</span
                  >
                </span>
              </div>
              <div
                class="grid grid-cols-[repeat(auto-fit,minmax(6.5rem,1fr))] gap-2"
              >
                <Button href={resolve("/settings/username")} size="sm">
                  <AtSignIcon />
                  {m.username_nav()}
                </Button>
                {#if !READ_ONLY}
                  <Button href={resolve("/settings/keys")} size="sm">
                    <KeyRoundIcon />
                    {m.keys_nav()}
                  </Button>
                {/if}
                <form method="POST" action={resolve("/sign-out")} class="grid">
                  <Button type="submit" size="sm">
                    <LogOutIcon />
                    {m.sign_out()}
                  </Button>
                </form>
              </div>
            {/if}
          </div>
        </div>
      </Drawer.Content>
    </Drawer.Root>
  </div>
</header>
