import type { PageLoad } from "./$types";

export const load: PageLoad = async ({ data }) => {
  if (data.kind === "contest") {
    return { ...data, View: (await import("./contest.svelte")).default };
  }
  if (data.kind === "election") {
    return { ...data, View: (await import("./election.svelte")).default };
  }
  if (data.kind === "day") {
    return { ...data, View: (await import("./day.svelte")).default };
  }
  return { ...data, View: (await import("./polity.svelte")).default };
};
