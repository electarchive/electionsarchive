import { version } from "$app/env";
import {
  defineCustomClientStrategy,
  isLocale,
} from "#lib/paraglide/runtime.js";

defineCustomClientStrategy("custom-edge", {
  getLocale: () => {
    const { lang } = document.documentElement;
    return isLocale(lang) ? lang : undefined;
  },
  setLocale: () => {},
});

const native = fetch.bind(globalThis);

Object.defineProperty(globalThis, "fetch", {
  configurable: true,
  writable: true,
  value: async (input: RequestInfo | URL, init?: RequestInit) => {
    if (typeof input === "string") {
      const url = new URL(input, location.href);
      if (
        url.origin === location.origin &&
        url.pathname.endsWith("/__data.json")
      ) {
        url.searchParams.set("build", version);
        return native(url, init);
      }
    }
    return native(input, init);
  },
});
