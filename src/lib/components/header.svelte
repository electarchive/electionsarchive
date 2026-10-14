<script lang="ts">
  import { resolve } from "$app/paths";
  import {
    LanguagesIcon,
    MonitorIcon,
    MoonIcon,
    SunIcon,
  } from "@lucide/svelte";
  import { setMode, userPrefersMode } from "mode-watcher";
  import { buttonVariants } from "#lib/components/ui/button/index.js";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { getLocale, setLocale } from "#lib/paraglide/runtime.js";

  const LOCALES = [
    { value: "en", label: "English" },
    { value: "tr", label: "Türkçe" },
  ] as const;

  const THEMES = [
    { value: "system", label: "System" },
    { value: "light", label: "Light" },
    { value: "dark", label: "Dark" },
  ] as const;
</script>

<header class="border-be">
  <div
    class="mx-auto flex items-center justify-between px-4 block-14 max-inline-7xl"
  >
    <a href={resolve("/")} class="font-semibold">
      Template for SvelteKit Projects
    </a>
    <div class="flex items-center gap-2">
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          class={buttonVariants({ variant: "outline", size: "icon" })}
        >
          <LanguagesIcon />
          <span class="sr-only">Change language</span>
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="end">
          <DropdownMenu.RadioGroup
            value={getLocale()}
            onValueChange={(value) => {
              const locale = LOCALES.find((option) => option.value === value);
              if (locale != null) {
                void setLocale(locale.value);
              }
            }}
          >
            {#each LOCALES as locale (locale.value)}
              <DropdownMenu.RadioItem value={locale.value}>
                {locale.label}
              </DropdownMenu.RadioItem>
            {/each}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Root>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          class={buttonVariants({ variant: "outline", size: "icon" })}
        >
          {#if userPrefersMode.current === "dark"}
            <MoonIcon />
          {:else if userPrefersMode.current === "light"}
            <SunIcon />
          {:else}
            <MonitorIcon />
          {/if}
          <span class="sr-only">Change theme</span>
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="end">
          <DropdownMenu.RadioGroup
            value={userPrefersMode.current}
            onValueChange={(value) => {
              const theme = THEMES.find((option) => option.value === value);
              if (theme != null) {
                setMode(theme.value);
              }
            }}
          >
            {#each THEMES as theme (theme.value)}
              <DropdownMenu.RadioItem value={theme.value}>
                {theme.label}
              </DropdownMenu.RadioItem>
            {/each}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    </div>
  </div>
</header>
