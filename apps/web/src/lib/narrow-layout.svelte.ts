import { browser } from "$app/env";
import { createSubscriber } from "svelte/reactivity";

let isNarrow: boolean | null = null;

const rootRem = () =>
  Number(getComputedStyle(document.documentElement).fontSize.slice(0, -2));

const subscribe = createSubscriber((update) => {
  let rem = rootRem();
  let width: number | null = null;
  const refresh = () => {
    const next = width == null ? null : width < 48 * rem;
    if (next !== isNarrow) {
      isNarrow = next;
      update();
    }
  };
  const resize = new ResizeObserver(([entry]) => {
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
  return isNarrow ?? document.body.clientWidth < 48 * rootRem();
}
