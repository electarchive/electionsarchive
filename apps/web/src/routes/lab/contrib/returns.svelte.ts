import type { Column, County } from "./data.js";
import { columns, contest, counties, integer } from "./data.js";

type Row = { county: County; cells: [string, string] };

export function parse(text: string) {
  const digits = text.replaceAll(",", "").trim();
  return /^\d+$/.test(digits) ? Number(digits) : null;
}

export function cellId(row: Row, column: Column) {
  return `${row.county.id}-${column}`;
}

export class Returns {
  #seed: (county: County, column: Column) => number;
  rows = $state<Array<Row>>([]);
  sums = $derived([this.total(0), this.total(1)] as const);
  off = $derived([
    this.sums[0] !== contest.candidates[0].total,
    this.sums[1] !== contest.candidates[1].total,
  ] as const);
  invalid = $derived(
    this.rows.some((row) =>
      columns.some((column) => parse(row.cells[column]) == null),
    ),
  );

  constructor(
    seed: (county: County, column: Column) => number = (county, column) =>
      county.votes[column],
  ) {
    this.#seed = seed;
    this.reset();
  }

  reset() {
    this.rows = counties.map((county) => ({
      county,
      cells: [
        integer.format(this.#seed(county, 0)),
        integer.format(this.#seed(county, 1)),
      ],
    }));
  }

  total(column: Column) {
    let sum = 0;
    for (const row of this.rows) sum += parse(row.cells[column]) ?? 0;
    return sum;
  }

  changed(row: Row, column: Column) {
    return parse(row.cells[column]) !== row.county.votes[column];
  }

  tidy(row: Row, column: Column) {
    const value = parse(row.cells[column]);
    if (value != null) row.cells[column] = integer.format(value);
  }

  find(id: string) {
    const row = this.rows.find((candidate) =>
      id.startsWith(`${candidate.county.id}-`),
    );
    if (row == null) return null;
    const column: Column = id.endsWith("-1") ? 1 : 0;
    return { row, column };
  }
}
