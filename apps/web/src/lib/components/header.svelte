<script lang="ts">
  import { resolve } from "$app/paths";
  import { MonitorIcon, MoonIcon, SunIcon } from "@lucide/svelte";
  import { setMode, userPrefersMode } from "mode-watcher";
  import Monogram from "#lib/components/monogram.svelte";
  import { buttonVariants } from "#lib/components/ui/button/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { m } from "#lib/paraglide/messages.js";

  const THEMES = [
    { value: "system", label: m.theme_system(), icon: MonitorIcon },
    { value: "light", label: m.theme_light(), icon: SunIcon },
    { value: "dark", label: m.theme_dark(), icon: MoonIcon },
  ] as const;
</script>

<header class="border-be">
  <div
    class="mx-auto flex items-center justify-between px-4 block-12 max-inline-7xl"
  >
    <a href={resolve("/")} class="flex items-center gap-2.5">
      <Monogram size={26} />
      <span class="text-lg font-[780] tracking-[-0.015em]">{m.wordmark()}</span>
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
      <DropdownMenu.Content align="end" class="inline-40">
        <DropdownMenu.RadioGroup
          value={userPrefersMode.current}
          onValueChange={(value) => {
            const theme = THEMES.find((option) => option.value === value);
            if (theme != null) {
              setMode(theme.value);
            }
          }}
        >
          <DropdownMenu.GroupHeading>{m.theme_menu()}</DropdownMenu.GroupHeading
          >
          {#each THEMES as theme (theme.value)}
            <DropdownMenu.RadioItem value={theme.value}>
              <theme.icon class="text-muted-foreground" />
              {theme.label}
            </DropdownMenu.RadioItem>
          {/each}
        </DropdownMenu.RadioGroup>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  </div>
</header>
