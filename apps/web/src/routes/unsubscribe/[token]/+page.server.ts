import { fail } from "@sveltejs/kit";
import * as v from "valibot";
import { LISTS, subscription, unsubscribe } from "#lib/server/notifications.js";
import type { Actions, PageServerLoad } from "./$types";

const listOf = (url: URL) => {
  const list = url.searchParams.get("list");
  return v.is(v.picklist(LISTS), list) ? list : null;
};

export const load: PageServerLoad = async ({ params, url, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const list = listOf(url);
  const found = list == null ? null : await subscription(params.token);
  return {
    list,
    subscribed:
      found == null
        ? null
        : list === "direct"
          ? found.direct_email
          : found.digest !== "off",
  };
};

export const actions: Actions = {
  default: async ({ params, url }) => {
    const list = listOf(url);
    if (list == null || !(await unsubscribe(params.token, list))) {
      return fail(404, { invalid: true });
    }
    return { done: list };
  },
};
