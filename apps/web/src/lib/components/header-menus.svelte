<script lang="ts">
  import type ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import type { Snippet } from "svelte";
  import { READ_ONLY } from "$app/env/public";
  import { afterNavigate } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import {
    AtSignIcon,
    ChevronRightIcon,
    KeyRoundIcon,
    LogInIcon,
    LogOutIcon,
    MonitorIcon,
    MoonIcon,
    SunIcon,
    UserIcon,
  } from "@lucide/svelte";
  import { setMode, userPrefersMode } from "mode-watcher";
  import { onMount } from "svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { Switch } from "#lib/components/ui/switch/index.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    links,
    current,
    signIn,
    initial,
    themeTrigger,
    accountTrigger,
    menuTrigger,
    signInButton,
  }: {
    links: Array<{
      href: string;
      label: string;
      about: string;
      icon: typeof ArrowLeftRightIcon;
    }>;
    current: (href: string) => "page" | undefined;
    signIn: string;
    initial: "theme" | "account" | "menu" | null;
    themeTrigger: Snippet<[Record<string, unknown>]>;
    accountTrigger: Snippet<[Record<string, unknown>]>;
    menuTrigger: Snippet<[Record<string, unknown>]>;
    signInButton: Snippet;
  } = $props();

  const THEMES = [
    { value: "system", label: m.theme_system(), icon: MonitorIcon },
    { value: "light", label: m.theme_light(), icon: SunIcon },
    { value: "dark", label: m.theme_dark(), icon: MoonIcon },
  ] as const;

  let isThemeOpen = $state(false);
  let isAccountOpen = $state(false);
  let isMenuOpen = $state(false);

  function theme(value: unknown) {
    const found = THEMES.find((option) => option.value === value);
    if (found != null) {
      setMode(found.value);
    }
  }

  onMount(() => {
    isThemeOpen = initial === "theme";
    isAccountOpen = initial === "account";
    isMenuOpen = initial === "menu";
  });

  afterNavigate(() => {
    isMenuOpen = false;
  });
</script>

<div class="flex items-center gap-1 max-lg:hidden">
  <DropdownMenu.Root bind:open={isThemeOpen}>
    <DropdownMenu.Trigger>
      {#snippet child({ props }: { props: Record<string, unknown> })}
        {@render themeTrigger(props)}
      {/snippet}
    </DropdownMenu.Trigger>
    <DropdownMenu.Content align="end" class="inline-56">
      <DropdownMenu.RadioGroup
        value={userPrefersMode.current}
        onValueChange={theme}
      >
        <DropdownMenu.GroupHeading>{m.theme_menu()}</DropdownMenu.GroupHeading>
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
    {@render signInButton()}
  {:else}
    <DropdownMenu.Root bind:open={isAccountOpen}>
      <DropdownMenu.Trigger>
        {#snippet child({ props }: { props: Record<string, unknown> })}
          {@render accountTrigger(props)}
        {/snippet}
      </DropdownMenu.Trigger>
      <DropdownMenu.Content align="end" class="inline-56">
        <DropdownMenu.Label class="grid gap-0.5">
          <span class="truncate text-foreground">{page.data.user.name}</span>
          <span class="truncate font-normal">{page.data.user.email}</span>
        </DropdownMenu.Label>
        <DropdownMenu.Separator />
        <DropdownMenu.Item>
          {#snippet child({ props })}
            <a {...props} href={resolve("/settings/profile")}>
              <AtSignIcon class="text-muted-foreground" />
              {m.profile_nav()}
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
    {#snippet child({ props }: { props: Record<string, unknown> })}
      {@render menuTrigger(props)}
    {/snippet}
  </Drawer.Trigger>
  <Drawer.Content class="sm:mx-auto sm:max-inline-xl">
    <Drawer.Title class="sr-only">{m.header_menu()}</Drawer.Title>
    <div
      class="grid gap-4 px-3 pbs-3 pbe-[calc(env(safe-area-inset-bottom)+1rem)]"
    >
      <nav class="grid" aria-label={m.header_menu()}>
        {#each links as link (link.href)}
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
          <p class="text-[0.8125rem]/snug text-pretty text-muted-foreground">
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
              <span class="truncate font-semibold">{page.data.user.name}</span>
              <span class="truncate text-muted-foreground"
                >{page.data.user.email}</span
              >
            </span>
          </div>
          <div
            class="grid grid-cols-[repeat(auto-fit,minmax(6.5rem,1fr))] gap-2"
          >
            <Button href={resolve("/settings/profile")} size="sm">
              <AtSignIcon />
              {m.profile_nav()}
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
