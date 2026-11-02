<script lang="ts">
  import LanguagesIcon from "@lucide/svelte/icons/languages";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import { LANGUAGES } from "#lib/languages.js";
  import { m } from "#lib/paraglide/messages.js";
  import {
    getLocale,
    isLocale,
    locales,
    setLocale,
  } from "#lib/paraglide/runtime.js";

  const current = getLocale();

  const names = (() => {
    try {
      return new Intl.DisplayNames([current], { type: "language" });
    } catch {
      return null;
    }
  })();

  const items = locales
    .map((locale) => {
      const label = LANGUAGES[locale] ?? locale;
      const local = locale === current ? undefined : names?.of(locale);
      return {
        value: locale,
        label,
        detail:
          local == null || local.toLowerCase() === label.toLowerCase()
            ? undefined
            : local,
        keywords: [locale, local ?? ""],
      };
    })
    .toSorted((a, b) => a.label.localeCompare(b.label, current));
</script>

{#if locales.length > 1}
  <div class="flex items-center gap-1.5">
    <LanguagesIcon class="shrink-0 block-4 inline-4" aria-hidden="true" />
    <Combobox
      {items}
      value={current}
      label={m.language_label()}
      placeholder={m.language_label()}
      search={m.language_search()}
      empty={m.language_empty()}
      class="bg-card block-7 inline-auto min-inline-36"
      onValueChange={(value) => {
        if (value != null && isLocale(value) && value !== current) {
          void setLocale(value);
        }
      }}
    />
  </div>
{/if}
