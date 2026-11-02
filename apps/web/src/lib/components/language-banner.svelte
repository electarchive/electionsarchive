<script lang="ts">
  import XIcon from "@lucide/svelte/icons/x";
  import { Button } from "#lib/components/ui/button/index.js";
  import { suggest } from "#lib/languages.js";
  import { m } from "#lib/paraglide/messages.js";
  import {
    cookieName,
    getLocale,
    isLocale,
    locales,
    setLocale,
  } from "#lib/paraglide/runtime.js";

  const KEY = "ea-language-banner";

  let target = $state<(typeof locales)[number] | null>(null);

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
      target = found;
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
    lang={target}
    role="region"
    aria-label={m.language_label({}, { locale: target })}
    class="border-be bg-card"
  >
    <div
      class="mx-auto flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 text-sm max-inline-7xl max-sm:px-3"
    >
      <p class="flex-1 text-pretty min-inline-0">
        {m.language_banner_text({}, { locale: target })}
      </p>
      <div class="flex items-center gap-1.5">
        <Button
          size="sm"
          onclick={() => {
            if (target != null) {
              void setLocale(target);
            }
          }}>{m.language_banner_switch({}, { locale: target })}</Button
        >
        <Button size="sm" variant="ghost" onclick={dismiss}
          ><XIcon aria-hidden="true" />{m.language_banner_dismiss(
            {},
            { locale: target },
          )}</Button
        >
      </div>
    </div>
  </div>
{/if}
