export function runs<Cell extends { kind: string; round: number }>(
  cells: Array<Cell>,
) {
  const grouped: Array<{
    kind: Cell["kind"];
    round: number;
    cells: Array<Cell>;
  }> = [];
  for (const cell of cells) {
    const last = grouped.at(-1);
    if (last?.kind === cell.kind) {
      last.cells.push(cell);
    } else {
      grouped.push({ kind: cell.kind, round: cell.round, cells: [cell] });
    }
  }
  return grouped;
}
