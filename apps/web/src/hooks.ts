import type { Reroute } from "@sveltejs/kit/hooks";
import { READ_ONLY } from "$app/env/public";
import { deLocalizeUrl } from "#lib/paraglide/runtime.js";

const CLOSED =
  /^\/(?:edit|proposals|review|workbench|uploads|settings\/keys|lab\/contrib)(?:\/|$)/u;

export const reroute: Reroute = (request) => {
  const { pathname } = deLocalizeUrl(request.url);
  return READ_ONLY && CLOSED.test(pathname) ? "/closed" : pathname;
};
