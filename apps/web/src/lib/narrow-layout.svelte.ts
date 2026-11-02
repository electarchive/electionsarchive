import { browser } from "$app/env";
import { createSubscriber } from "svelte/reactivity";

let isNarrow: boolean | null = null;

const rootRem = () =>
  Number(getComputedStyle(document.documentElement).fontSize.slice(0, -2));

const rootScale = () => {
  const size = document.documentElement.style.fontSize;
  return size.endsWith("%") ? Number(size.slice(0, -1)) / 100 : 1;
};

const subscribe = createSubscriber((update) => {
  let rem: number | null = null;
  let width: number | null = null;
  const refresh = () => {
    const next = width == null || rem == null ? null : width < 48 * rem;
    if (next !== isNarrow) {
      isNarrow = next;
      update();
    }
  };
  const resize = new ResizeObserver(([entry]) => {
    rem ??= rootRem();
    width = entry?.contentBoxSize[0]?.inlineSize ?? width;
    refresh();
  });
  const rootStyle = new MutationObserver(() => {
    rem = rootRem();
    refresh();
  });
  resize.observe(document.body);
  rootStyle.observe(document.documentElement, { attributeFilter: ["style"] });

  return () => {
    isNarrow = null;
    resize.disconnect();
    rootStyle.disconnect();
  };
});

/** Matches the max-md: container query on body, which follows the text size. */
export function isNarrowLayout(): boolean {
  if (!browser) {
    return false;
  }

  subscribe();
  return isNarrow ?? matchMedia(`(width < ${48 * rootScale()}rem)`).matches;
}
