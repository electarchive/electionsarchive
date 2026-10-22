import { unflatten } from "devalue";
import { pages } from "../../pages.js";
import { page } from "./state.svelte.js";

const SERVER = ["match", "by", "vs", "round", "group", "in"];

function key(path: string, params: URLSearchParams) {
  const kept = SERVER.flatMap((name) => {
    const value = params.get(name);
    return value == null || value === "" ? [] : [`${name}=${value}`];
  });
  return `${path.replace(/\/$/, "")}?${kept.join("&")}`;
}

const index = new Map<string, { slug: string; file: number }>();
for (const showcase of pages) {
  for (const [file, view] of showcase.views.entries()) {
    const url = new URL(`/${view}`, location.origin);
    const at = key(url.pathname.slice(1), url.searchParams);
    if (!index.has(at)) {
      index.set(at, { slug: showcase.slug, file });
    }
  }
}

export const slug = document.documentElement.dataset.showcase ?? pages[0].slug;
export const showcase = pages.find((entry) => entry.slug === slug) ?? pages[0];
export const notice = $state<{ text: string | null; at: number }>({
  text: null,
  at: 0,
});

const views = new Map<string, Promise<Record<string, unknown>>>();

function view(target: { slug: string; file: number }) {
  const at = `${target.slug}/${target.file}`;
  let loading = views.get(at);
  if (loading == null) {
    loading = fetch(`/showcase/${target.slug}/views/${target.file}.json`)
      .then((response) => response.json() as Promise<{ nodes: Array<unknown> }>)
      .then(({ nodes }) =>
        Object.assign(
          {},
          ...nodes.map(
            (node) =>
              unflatten(node as Parameters<typeof unflatten>[0]) as object,
          ),
        ),
      );
    views.set(at, loading);
  }
  return loading;
}

let calls: Promise<Record<string, unknown>> | null = null;

export async function recorded(at: string) {
  calls ??= fetch(`/showcase/${slug}/rpc.json`).then(
    (response) => response.json() as Promise<Record<string, unknown>>,
  );
  return (await calls)[at];
}

export function locate(href: string | URL) {
  const url = new URL(href, page.url);
  if (url.origin !== location.origin) {
    return null;
  }
  if (url.pathname.startsWith("/showcase/")) {
    return { url, hit: null, outside: true };
  }
  const path = url.pathname.slice(1);
  return {
    url,
    hit: index.get(key(path, url.searchParams)) ?? null,
    outside: false,
  };
}

function address(target: { slug: string; file: number }, url: URL) {
  const first = new URL(
    `/${pages.find((entry) => entry.slug === target.slug)?.views[0] ?? ""}`,
    location.origin,
  );
  const search = new URLSearchParams(url.search);
  if (url.pathname !== first.pathname) {
    search.set("p", url.pathname.slice(1));
  }
  const query = search.toString();
  return `/showcase/${target.slug}/${query === "" ? "" : `?${query}`}`;
}

export function archive(location_: Location | URL) {
  const params = new URLSearchParams(location_.search);
  const path = params.get("p") ?? showcase.views[0].split("?")[0];
  params.delete("p");
  const query = params.toString();
  return new URL(`/${path}${query === "" ? "" : `?${query}`}`, location.origin);
}

export function stop() {
  notice.text = "This preview ends here. The full archive opens at launch.";
  notice.at = Date.now();
}

const listeners = new Set<() => void>();

export function after(callback: () => void) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

export async function preload(href: string | URL) {
  const found = locate(href);
  if (found?.hit == null) {
    return null;
  }
  return view(found.hit);
}

export async function show(
  href: string | URL,
  options: {
    replaceState?: boolean;
    noScroll?: boolean;
    state?: Record<string, unknown>;
  } = {},
) {
  const found = locate(href);
  if (found == null || found.outside) {
    location.assign(String(href));
    return;
  }
  if (found.hit == null) {
    stop();
    return;
  }
  if (found.hit.slug !== slug) {
    location.assign(address(found.hit, found.url));
    return;
  }
  const data = await view(found.hit);
  const isMove = found.url.pathname !== page.url.pathname;
  const swap = () => {
    history[options.replaceState === true ? "replaceState" : "pushState"](
      JSON.parse(JSON.stringify(options.state ?? {})),
      "",
      address(found.hit, found.url),
    );
    page.url = found.url;
    page.params = {
      path: found.url.pathname.slice(1).replace(/^(parties|people)\//, ""),
      slug: found.url.pathname.split("/").at(-1) ?? "",
    };
    page.state = options.state ?? {};
    page.data = data;
    if (isMove && options.noScroll !== true) {
      scrollTo({ top: 0 });
    }
    for (const listener of listeners) {
      listener();
    }
  };
  if (
    isMove &&
    "startViewTransition" in document &&
    !matchMedia("(prefers-reduced-motion: reduce)").matches
  ) {
    await document.startViewTransition(swap).updateCallbackDone;
  } else {
    swap();
  }
}

export async function start() {
  const [, initial] = showcase.views[0].split("?");
  if (location.search === "" && initial != null) {
    history.replaceState(null, "", `?${initial}`);
  }
  const url = archive(location);
  const found = locate(url);
  page.url = url;
  page.params = {
    path: url.pathname.slice(1).replace(/^(parties|people)\//, ""),
    slug: url.pathname.split("/").at(-1) ?? "",
  };
  page.data = await view(found?.hit ?? { slug, file: 0 });
  addEventListener("popstate", () => {
    const next = archive(location);
    const hit = locate(next)?.hit;
    if (hit?.slug === slug) {
      void view(hit).then((data) => {
        page.url = next;
        page.data = data;
      });
    }
  });
}
