import type { FORMS } from "#lib/noun-forms.js";
import type * as English from "#lib/paraglide/messages/en.js";
import type { Locale } from "#lib/paraglide/runtime.js";
import { baseLocale } from "#lib/paraglide/runtime.js";

export type Messages = typeof English;

const loaders: Record<
  Exclude<Locale, typeof baseLocale>,
  () => Promise<Messages>
> = {
  tr: async () => import("#lib/paraglide/messages/tr.js"),
  kmr: async () => import("#lib/paraglide/messages/kmr.js"),
  de: async () => import("#lib/paraglide/messages/de.js"),
  fr: async () => import("#lib/paraglide/messages/fr.js"),
  "pt-BR": async () => import("#lib/paraglide/messages/pt-BR.js"),
};

export const dictionary: {
  messages: Messages | null;
  nouns: typeof FORMS | null;
} = { messages: null, nouns: null };

export async function translations(locale: Locale) {
  return locale === baseLocale ? null : loaders[locale]();
}

export async function use(locale: Locale) {
  if (locale === baseLocale) {
    return;
  }
  const [messages, { FORMS: nouns }] = await Promise.all([
    loaders[locale](),
    import("#lib/noun-forms.js"),
  ]);
  dictionary.messages = messages;
  dictionary.nouns = nouns;
}
