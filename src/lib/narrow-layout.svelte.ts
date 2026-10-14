import { browser } from "$app/env";
import { createSubscriber } from "svelte/reactivity";

const subscribe = createSubscriber((update) => {
  const resize = new ResizeObserver(update);
  const rootStyle = new MutationObserver(update);
  resize.observe(document.body);
  rootStyle.observe(document.documentElement, { attributeFilter: ["style"] });

  return () => {
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
  const rem = Number(
    getComputedStyle(document.documentElement).fontSize.slice(0, -2),
  );

  return document.body.clientWidth < 48 * rem;
}
