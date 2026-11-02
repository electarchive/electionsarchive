export const HANDLE = /^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$/u;

export type Piece =
  | { kind: "text"; value: string }
  | { kind: "mention"; value: string }
  | { kind: "reference"; value: string };

const TOKENS =
  /(?<![\w@/])@([a-z0-9](?:[a-z0-9-]{0,28}[a-z0-9])?)(?![\w-])|(?<![\w&/])#([1-9]\d{3,8})\b/giu;

export function piecesOf(text: string) {
  const pieces: Array<Piece> = [];
  let at = 0;
  for (const match of text.matchAll(TOKENS)) {
    const handle = match[1]?.toLowerCase();
    if (handle != null && !HANDLE.test(handle)) {
      continue;
    }
    if (match.index > at) {
      pieces.push({ kind: "text", value: text.slice(at, match.index) });
    }
    pieces.push(
      handle == null
        ? { kind: "reference", value: match[2] ?? "" }
        : { kind: "mention", value: handle },
    );
    at = match.index + match[0].length;
  }
  if (at < text.length) {
    pieces.push({ kind: "text", value: text.slice(at) });
  }
  return pieces;
}

export function safeHref(url: string) {
  try {
    const parsed = new URL(url, "https://electionsarchive.com");
    return ["http:", "https:", "mailto:"].includes(parsed.protocol)
      ? url
      : null;
  } catch {
    return null;
  }
}
