<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import {
    ArrowLeftRightIcon,
    GaugeIcon,
    LayoutDashboardIcon,
    LogOutIcon,
    MonitorIcon,
    MoonIcon,
    SunIcon,
    UserIcon,
  } from "@lucide/svelte";
  import { setMode, userPrefersMode } from "mode-watcher";
  import Monogram from "#lib/components/monogram.svelte";
  import Search from "#lib/components/search.svelte";
  import { buttonVariants } from "#lib/components/ui/button/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const THEMES = [
    { value: "system", label: m.theme_system(), icon: MonitorIcon },
    { value: "light", label: m.theme_light(), icon: SunIcon },
    { value: "dark", label: m.theme_dark(), icon: MoonIcon },
  ] as const;
</script>

<header class="border-be">
  <div
    class="mx-auto flex items-center justify-between gap-3 px-4 block-12 max-inline-7xl max-sm:gap-2 max-sm:px-3"
  >
    <div class="relative">
      <a
        href={resolve("/")}
        class="flex items-center gap-2.5 max-[24rem]:gap-2"
      >
        <Monogram size={26} />
        <span
          class="text-lg font-[780] tracking-[-0.015em] whitespace-nowrap max-[24rem]:text-base"
          >{m.wordmark()}</span
        >
      </a>
      <Tooltip.Root delayDuration={150}>
        <Tooltip.Trigger
          class="absolute -inset-e-3 inset-bs-[calc(100%-0.375rem)] -rotate-3 cursor-help rounded-[2px] bg-marker px-1 text-[0.625rem]/[0.9rem] font-[780] tracking-[0.02em] text-[#15171a] transition-transform hover:rotate-0 focus-visible:outline-2 focus-visible:outline-ring motion-reduce:transition-none"
          >{m.beta_label()}</Tooltip.Trigger
        >
        <Tooltip.Content
          side="bottom"
          align="start"
          collisionPadding={8}
          class="leading-snug max-inline-60"
          >{m.beta_definition()}</Tooltip.Content
        >
      </Tooltip.Root>
    </div>
    <div class="flex items-center gap-2 max-sm:gap-0">
      <Search />
      <a
        href={resolve("/compare")}
        class={cn(buttonVariants({ variant: "ghost" }), "max-sm:px-2")}
        aria-current={page.url.pathname === "/compare" ? "page" : undefined}
      >
        <ArrowLeftRightIcon />
        <span class="max-sm:sr-only">{m.versus_action()}</span>
      </a>
      <a
        href={resolve("/coverage")}
        class={cn(buttonVariants({ variant: "ghost" }), "max-sm:px-2")}
        aria-current={page.url.pathname === "/coverage" ? "page" : undefined}
      >
        <GaugeIcon />
        <span class="max-sm:sr-only">{m.coverage_nav()}</span>
      </a>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          class={buttonVariants({ variant: "ghost", size: "icon" })}
        >
          {#if userPrefersMode.current === "dark"}
            <MoonIcon />
          {:else if userPrefersMode.current === "light"}
            <SunIcon />
          {:else}
            <MonitorIcon />
          {/if}
          <span class="sr-only">{m.theme_menu()}</span>
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="end" class="inline-56">
          <DropdownMenu.RadioGroup
            value={userPrefersMode.current}
            onValueChange={(value) => {
              const theme = THEMES.find((option) => option.value === value);
              if (theme != null) {
                setMode(theme.value);
              }
            }}
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
        <a
          href="{resolve('/sign-in')}?next={encodeURIComponent(
            page.url.pathname,
          )}"
          class={cn(buttonVariants({ variant: "ghost" }), "max-sm:px-2.5")}
          >{m.sign_in_submit()}</a
        >
      {:else}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger
            class={buttonVariants({ variant: "ghost", size: "icon" })}
          >
            <UserIcon />
            <span class="sr-only">{m.account_menu()}</span>
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
                <a {...props} href={resolve("/workbench")}>
                  <LayoutDashboardIcon class="text-muted-foreground" />
                  {m.workbench_nav()}
                </a>
              {/snippet}
            </DropdownMenu.Item>
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
  </div>
</header>
