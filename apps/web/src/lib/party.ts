const NEUTRALS = [78, 56, 36].map(
  (amount) =>
    `color-mix(in oklab, var(--muted-foreground) ${amount}%, var(--card))`,
);

const ANSWERS = new Map([
  ["answer:yes", "var(--yes)"],
  ["answer:sì", "var(--yes)"],
  ["answer:evet", "var(--yes)"],
  ["answer:no", "var(--no)"],
  ["answer:hayır", "var(--no)"],
]);

export function swatch(color: string | null | undefined, key: string) {
  if (color != null) {
    return `light-dark(oklch(from ${color} min(l, 0.86) c h), oklch(from ${color} max(l, 0.52) c h))`;
  }
  const answer = ANSWERS.get(key);
  if (answer != null) {
    return answer;
  }
  let hash = 0;
  for (const character of key) {
    hash = (hash * 31 + (character.codePointAt(0) ?? 0)) % 9973;
  }
  return NEUTRALS[hash % NEUTRALS.length] ?? "var(--muted-foreground)";
}
