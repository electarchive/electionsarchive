import type { Part } from "svelte-islands";
import { load as fetched } from "svelte-islands";
import type { PageLoad } from "./$types";

async function early<T>(found: Part<T> | null) {
  return found?.name === "election.sweep" ? fetched(found) : found;
}

export const load: PageLoad = async ({ data }) => {
  if (data.kind === "contest") {
    const { contest, roster } = data;
    const [View, rows] = await Promise.all([
      import("./contest.svelte"),
      roster == null ? null : fetched(roster).catch(() => null),
    ]);
    return {
      ...data,
      contest:
        rows == null || contest.places == null
          ? contest
          : { ...contest, places: { ...contest.places, rows } },
      View: View.default,
    };
  }
  if (data.kind === "election") {
    const { winners } = data.sections;
    const isStreamed = winners instanceof Promise;
    const settled = isStreamed ? winners.then(early) : early(winners);
    const [View, ready] = await Promise.all([
      import("./election.svelte"),
      isStreamed ? null : settled,
    ]);
    return {
      ...data,
      sections: { ...data.sections, winners: isStreamed ? settled : ready },
      View: View.default,
    };
  }
  if (data.kind === "day") {
    return { ...data, View: (await import("./day.svelte")).default };
  }
  return { ...data, View: (await import("./polity.svelte")).default };
};
