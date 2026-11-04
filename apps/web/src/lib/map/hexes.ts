export const covering = <
  T extends { isPartial: boolean; cells: Array<{ k: number }> },
>(
  layouts: Array<T> | null | undefined,
  keys: { has: (k: number) => boolean; size: number },
) =>
  layouts?.find((layout) => {
    let covered = 0;
    for (const cell of layout.cells) {
      if (keys.has(cell.k)) {
        covered += 1;
      }
    }
    return layout.isPartial
      ? covered > 1 && covered >= keys.size * 0.9
      : covered > 0 && covered >= layout.cells.length * 0.9;
  }) ?? null;

export const shapesOf = <
  T extends {
    isPartial: boolean;
    cells: Array<{ k: number }>;
    cartogram: unknown;
  },
>(
  layouts: Array<T> | null | undefined,
  keys: { has: (k: number) => boolean; size: number },
) => {
  const layout = covering(layouts, keys);
  return layout == null
    ? []
    : [
        ...(layout.cartogram == null ? [] : ["cartogram" as const]),
        "hexes" as const,
      ];
};
