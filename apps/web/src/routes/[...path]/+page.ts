import type { Part } from "svelte-islands";
import { browser } from "$app/env";
import { load as fetched } from "svelte-islands";
import type { Primed } from "#lib/primed.js";
import { prime } from "#lib/primed.js";
import { makeQueryClient } from "#lib/query-client.js";
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

type Shapes = Awaited<Extract<PageServerData, { kind: "contest" }>["shapes"]>;

async function encircle({ rows: part, surround, ...rest }: Shapes) {
  const rows = part == null ? null : await fetched(part).catch(() => null);
  return {
    ...rest,
    surround:
      rows == null || surround?.places == null
        ? surround
        : { ...surround, places: { ...surround.places, rows } },
  };
}

function encircled(
  shapes: Shapes | Promise<Shapes>,
): ReturnType<typeof encircle> | Awaited<ReturnType<typeof encircle>> {
  if (shapes instanceof Promise) {
    return shapes.then(encircle);
  }
  const { rows, ...rest } = shapes;
  return rows == null ? rest : encircle(shapes);
}

async function due<T>(promise: Promise<T>) {
  if (!browser) {
    return promise;
  }
  return new Promise<T>((done, fail) => {
    const timer = setTimeout(
      fail,
      120_000,
      new Error("The results timed out."),
    );
    void promise
      .then(done)
      .catch(fail)
      .finally(() => clearTimeout(timer));
  });
}

async function early<T>(found: Part<T> | null) {
  return found?.name === "election.sweep" ? fetched(found) : found;
}

async function unpacked(found: Part<Primed> | null): Promise<Primed> {
  return found == null ? [] : fetched(found).catch(() => []);
}

async function unfolded(
  found: Part<Primed> | Promise<Part<Primed> | null> | null,
): Promise<Primed> {
  const primed = await unpacked(await found);
  if (browser) {
    prime(makeQueryClient(), primed);
  }
  return primed;
}

export const load: PageLoad = async ({ data }) => {
  if (data.kind === "contest") {
    const { body } = data;
    const primed = unfolded(data.primed);
    const view = import("./contest.svelte");
    const shapes = encircled(data.shapes);
    if (body instanceof Promise) {
      return {
        ...data,
        body: due(body.then(seat)),
        shapes,
        primed: data.primed instanceof Promise ? primed : await primed,
        View: (await view).default,
      };
    }
    const [View, ready] = await Promise.all([view, seat(body)]);
    return {
      ...data,
      body: ready,
      shapes,
      primed: data.primed instanceof Promise ? primed : await primed,
      View: View.default,
    };
  }
  if (data.kind === "election") {
    const { winners } = data.sections;
    const isStreamed = winners instanceof Promise;
    const settled = isStreamed ? winners.then(early) : early(winners);
    const primed = unfolded(data.primed);
    const [View, ready] = await Promise.all([
      import("./election.svelte"),
      isStreamed ? null : settled,
    ]);
    return {
      ...data,
      sections: { ...data.sections, winners: isStreamed ? settled : ready },
      primed: data.primed instanceof Promise ? primed : await primed,
      View: View.default,
    };
  }
  if (data.kind === "day") {
    return { ...data, View: (await import("./day.svelte")).default };
  }
  const primed = unfolded(data.polity.primed);
  const View = await import("./polity.svelte");
  return {
    ...data,
    polity: { ...data.polity, primed: await primed },
    View: View.default,
  };
};
