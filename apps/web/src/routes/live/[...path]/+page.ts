import { opened, optional } from "#lib/live/board.js";
import type { PageLoad } from "./$types";

export const load: PageLoad = async ({ data }) => {
  const { board, geography, ballot, points, ...rest } = data;
  const [night, map, listed, trail] = await Promise.all([
    opened(board),
    optional(geography),
    optional(ballot),
    optional(points),
  ]);
  return {
    ...rest,
    ...night,
    geography: map,
    ballot: listed,
    points: trail ?? [],
    board: board.stable,
  };
};
