import { opened, optional } from "#lib/live/board.js";
import type { PageLoad } from "./$types";

export const load: PageLoad = async ({ data }) => {
  const { board, cartogram, holders, ...rest } = data;
  const [night, hexes] = await Promise.all([
    opened(board),
    optional(cartogram),
  ]);
  return {
    ...rest,
    ...night,
    cartogram: hexes,
    control: holders ?? night.control,
  };
};
