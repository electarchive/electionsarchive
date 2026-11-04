import { load as fetched } from "svelte-islands";
import { join } from "#lib/live/board.js";
import type { PageLoad } from "./$types";

export const load: PageLoad = async ({ data }) => {
  if (data.board == null) {
    return data;
  }
  const { board, ...rest } = data;
  const [stable, changing] = await Promise.all([
    fetched(board.stable),
    fetched(board.changing),
  ]);
  return {
    ...rest,
    ...join(board.head, stable, changing),
    board: board.stable,
  };
};
