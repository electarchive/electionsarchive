import { getLocale } from "#lib/paraglide/runtime.js";

export type Native = { text: string; lang: string } | null;

type Shown = { text: string; lang: string | undefined };

function scriptOf(lang: string) {
  try {
    return new Intl.Locale(lang).maximize().script ?? null;
  } catch {
    return null;
  }
}

function tagOf(lang: string) {
  return lang === "und" ? undefined : lang;
}

export function ordered(
  name: string,
  native: Native,
): { primary: Shown; secondary: Shown | null } {
  if (native == null || native.text === "") {
    return { primary: { text: name, lang: undefined }, secondary: null };
  }
  const script = native.lang === "und" ? null : scriptOf(native.lang);
  const isNativeFirst =
    script != null && script !== "Latn" && scriptOf(getLocale()) === script;
  const local = { text: native.text, lang: tagOf(native.lang) };
  return isNativeFirst
    ? { primary: local, secondary: { text: name, lang: `${native.lang}-Latn` } }
    : { primary: { text: name, lang: undefined }, secondary: local };
}

export function titled(name: string, native: Native) {
  const { primary, secondary } = ordered(name, native);
  return secondary == null
    ? primary.text
    : `${primary.text} (${secondary.text})`;
}
