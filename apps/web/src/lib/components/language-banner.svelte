<script lang="ts">
  import XIcon from "@lucide/svelte/icons/x";
  import type { Messages } from "#lib/dictionary.js";
  import type { Locale } from "#lib/paraglide/runtime.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { translations } from "#lib/dictionary.js";
  import { suggest } from "#lib/languages.js";
  import {
    cookieName,
    getLocale,
    isLocale,
    locales,
    setLocale,
  } from "#lib/paraglide/runtime.js";

  const KEY = "ea-language-banner";

  let target = $state.raw<{ locale: Locale; strings: Messages } | null>(null);

  $effect(() => {
    if (
      document.cookie
        .split("; ")
        .some((part) => part.startsWith(`${cookieName}=`))
    ) {
      return;
    }
    try {
      if (localStorage.getItem(KEY) != null) {
        return;
      }
    } catch {
      return;
    }
    const found = suggest(navigator.languages, locales);
    if (found != null && isLocale(found) && found !== getLocale()) {
      void translations(found).then((strings) => {
        if (strings != null) {
          target = { locale: found, strings };
        }
      });
    }
  });

  function dismiss() {
    target = null;
    try {
      localStorage.setItem(KEY, "1");
    } catch {}
  }
</script>

{#if target != null}
  <div
    lang={target.locale}
    role="region"
    aria-label={target.strings.language_label({})}
    class="border-be bg-card"
  >
    <div
      class="mx-auto flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 text-sm max-inline-7xl max-sm:px-3"
    >
      <p class="flex-1 text-pretty min-inline-0">
        {target.strings.language_banner_text({})}
      </p>
      <div class="flex items-center gap-1.5">
        <Button
          size="sm"
          onclick={() => {
            if (target != null) {
              void setLocale(target.locale);
            }
          }}>{target.strings.language_banner_switch({})}</Button
        >
        <Button size="sm" variant="ghost" onclick={dismiss}
          ><XIcon aria-hidden="true" />{target.strings.language_banner_dismiss(
            {},
          )}</Button
        >
      </div>
    </div>
  </div>
{/if}
