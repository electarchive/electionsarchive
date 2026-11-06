import type { Part } from "svelte-islands";
import { load as fetched } from "svelte-islands";
import type { PageLoad, PageServerData } from "./$types";

async function seat(
  body: Awaited<Extract<PageServerData, { kind: "contest" }>["body"]>,
) {
  const { contest, roster } = body;
  const rows = roster == null ? null : await fetched(roster).catch(() => null);
  return rows == null || contest.places == null
    ? body
    : { ...body, contest: { ...contest, places: { ...contest.places, rows } } };
}

async function early<T>(found: Part<T> | null) {
  return found?.name === "election.sweep" ? fetched(found) : found;
}

export const load: PageLoad = async ({ data }) => {
  if (data.kind === "contest") {
    const { body } = data;
    const view = import("./contest.svelte");
    if (body instanceof Promise) {
      return { ...data, body: body.then(seat), View: (await view).default };
    }
    const [View, ready] = await Promise.all([view, seat(body)]);
    return { ...data, body: ready, View: View.default };
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
