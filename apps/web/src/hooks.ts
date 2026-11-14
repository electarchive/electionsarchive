import type { Reroute } from "@sveltejs/kit/hooks";
import { READ_ONLY } from "$app/env/public";
import { islands } from "svelte-islands";

const CLOSED = /^\/workbench(?:\/|$|\.)/u;

export const reroute: Reroute = (request) => {
  const { pathname } = request.url;
  return READ_ONLY && CLOSED.test(pathname) ? "/closed" : pathname;
};

export const transport = islands();
