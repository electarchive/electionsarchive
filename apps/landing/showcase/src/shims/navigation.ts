import { onDestroy } from "svelte";
import { page } from "./state.svelte.js";
import { after, archive, preload, show } from "./router.svelte.js";

export function goto(
  href: string | URL,
  options: {
    replaceState?: boolean;
    noScroll?: boolean;
    state?: Record<string, unknown>;
  } = {},
) {
  return show(href, options);
}

export async function preloadData(href: string) {
  const data = await preload(href);
  return data == null
    ? ({ type: "redirect", location: href } as const)
    : ({ type: "loaded", status: 200, data } as const);
}

export async function preloadCode() {}

export function replaceState(
  url: string | URL,
  state: Record<string, unknown> = {},
) {
  history.replaceState(JSON.parse(JSON.stringify(state)), "", url);
  page.url = archive(location);
  page.state = state;
}

export function pushState(
  url: string | URL,
  state: Record<string, unknown> = {},
) {
  history.pushState(
    JSON.parse(JSON.stringify(state)),
    "",
    url === "" ? location.href : url,
  );
  page.url = archive(location);
  page.state = state;
}

export function afterNavigate(callback: () => void) {
  const off = after(callback);
  onDestroy(() => {
    off();
  });
}

export function onNavigate() {}

export function beforeNavigate() {}

export async function invalidate() {}

export async function invalidateAll() {}

export function disableScrollHandling() {}
