<script lang="ts">
  import type { Prose } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";

  const { note }: { note: Prose } = $props();

  const languageName = new Intl.DisplayNames([getLocale()], {
    type: "language",
  });
  const named = (tag: string) => languageName.of(tag) ?? tag;
  const split = (message: (inputs: { language: string }) => string) =>
    message({ language: "\u0000" }).split("\u0000");

  let swapped = $state(false);

  const translated = $derived(note.source_lang != null);
  const shownLang = $derived(swapped ? (note.source_lang ?? "en") : note.lang);
  const marker = $derived(
    translated
      ? split(
          note.origin === "machine"
            ? m.note_translated_machine
            : m.note_translated,
        )
      : note.lang === getLocale() || note.lang === getLocale().split("-")[0]
        ? null
        : split(m.note_original),
  );
</script>

<span lang={shownLang} dir="auto"
  >{swapped ? (note.alternate ?? note.body) : note.body}</span
>
{#if marker != null}
  <span class="block text-xs text-muted-foreground"
    >{marker[0]}<bdi
      >{named(translated ? (note.source_lang ?? "") : note.lang)}</bdi
    >{marker[1]}{#if translated && note.stale}
      {m.note_translated_outdated()}{/if}{#if note.alternate != null}
      <button
        type="button"
        class="underline decoration-border underline-offset-2 hover:decoration-foreground"
        onclick={() => (swapped = !swapped)}
        >{translated
          ? swapped
            ? m.note_show_translation()
            : m.note_show_original()
          : swapped
            ? m.note_read_original()
            : m.note_read_english()}</button
      >{/if}</span
  >
{/if}
