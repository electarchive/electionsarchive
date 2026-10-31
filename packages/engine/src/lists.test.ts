import { describe, expect, it } from "vitest";
import acre from "./fixtures/acre-2022-lists.json" with { type: "json" };
import germany from "./fixtures/germany-2025-lists.json" with { type: "json" };
import germanyEuropean from "./fixtures/germany-ep-2024-lists.json" with { type: "json" };
import italy from "./fixtures/italy-2022-lists.json" with { type: "json" };
import zurich from "./fixtures/zurich-2023-lists.json" with { type: "json" };
import { cover, fill, proclaim } from "./lists.ts";

const byText = (a: string, b: string) => (a < b ? -1 : Number(a > b));
const closed = {
  order: "closed",
  exhausted: "vacant",
  vacancy: "next_on_list",
} as const;
const declared = (members: Array<{ id: string; elected: boolean }>) =>
  members.filter(({ elected }) => elected).map(({ id }) => id);

describe("closed lists", () => {
  it("fills seats in printed order, skipping members seated elsewhere", () => {
    const members = [
      { id: "A", rank: 1 },
      { id: "B", rank: 2 },
      { id: "C", rank: 3 },
      { id: "D", rank: 4 },
    ];
    expect(fill(closed, 2, members, new Set(["A"]))).toEqual({
      elected: ["B", "C"],
      short: 0,
      tie: undefined,
    });
    expect(fill(closed, 5, members).short).toBe(1);
  });

  it("reproduces Germany's 2024 European lists", () => {
    for (const list of germanyEuropean) {
      expect(fill(closed, list.seats, list.members).elected, list.list).toEqual(
        declared(list.members),
      );
    }
  });
});

describe("constituency winners first", () => {
  it("covers winners by their share up to the party's seats", () => {
    const winners = [
      { id: "A", share: 0.31 },
      { id: "B", share: 0.42 },
      { id: "C", share: 0.29 },
    ];
    expect(cover("covered", 2, winners).covered).toEqual(["B", "A"]);
    expect(cover("all", 2, winners)).toEqual({
      covered: ["A", "B", "C"],
      overhang: 1,
      tie: undefined,
    });
  });

  it("reproduces who took each seat of the 2025 Bundestag's Land lists", () => {
    const rules = { ...closed, constituencies: "covered" } as const;
    let listSeats = 0;
    for (const list of germany) {
      const { covered } = cover(
        "covered",
        list.seats,
        list.winners.map(({ id, votes, valid }) => ({
          id,
          share: votes / valid,
        })),
      );
      const label = `${list.land} ${list.party}`;
      expect(covered.toSorted(byText), label).toEqual(
        list.winners
          .filter(({ seated }) => seated)
          .map(({ id }) => id)
          .toSorted(byText),
      );
      const seats = list.seats - covered.length;
      listSeats += seats;
      expect(
        fill(rules, seats, list.members, new Set(covered)).elected,
        label,
      ).toEqual(declared(list.members));
    }
    expect(listSeats).toBe(354);
  });
});

describe("multiple candidacies", () => {
  it("keeps a member where the list's share is lowest", () => {
    const rules = { ...closed, multiple: "lowest_share" } as const;
    const results = proclaim(rules, [
      {
        id: "X",
        seats: 1,
        share: 0.2,
        candidates: [
          { id: "A", rank: 1 },
          { id: "B", rank: 2 },
        ],
      },
      {
        id: "Y",
        seats: 1,
        share: 0.1,
        candidates: [
          { id: "A", rank: 1 },
          { id: "C", rank: 2 },
        ],
      },
    ]);
    expect(results.get("X")?.elected).toEqual(["B"]);
    expect(results.get("Y")?.elected).toEqual(["A"]);
  });

  it("reproduces the 2022 Chamber's plurinominal proclamations except Fratelli d'Italia's moved seats", () => {
    const rules = {
      ...closed,
      constituencies: "skip",
      multiple: "lowest_share",
      exhausted: "elsewhere",
    } as const;
    const slates = italy.colleges.flatMap(({ college, lists }) =>
      lists.map((list) => ({
        id: `${college}: ${list.list}`,
        seats: list.seats,
        share: list.votes / list.valid,
        candidates: list.members,
        expected: declared(list.members),
      })),
    );
    const results = proclaim(rules, slates, new Set(italy.winners));
    const transferred = new Map<string, number>();
    for (const { college, list } of italy.transferred) {
      const id = `${college}: ${list}`;
      transferred.set(id, (transferred.get(id) ?? 0) + 1);
    }
    const moved = new Set(
      [
        "Emilia-Romagna - P01",
        "Emilia-Romagna - P02",
        "Toscana - P01",
        "Toscana - P02",
      ].map(
        (college) =>
          `Chamber of Deputies, ${college}: FRATELLI D'ITALIA CON GIORGIA MELONI`,
      ),
    );
    for (const slate of slates) {
      const result = results.get(slate.id);
      if (moved.has(slate.id)) {
        expect(result?.elected, slate.id).not.toEqual(slate.expected);
        continue;
      }
      expect(result?.elected, slate.id).toEqual(slate.expected);
      expect(result?.short, slate.id).toBe(transferred.get(slate.id) ?? 0);
    }
  });
});

describe("open lists", () => {
  it("reports a tie at the last seat", () => {
    expect(
      fill({ ...closed, order: "open" }, 1, [
        { id: "A", votes: 10 },
        { id: "B", votes: 10 },
      ]),
    ).toEqual({ elected: [], short: 0, tie: ["A", "B"] });
  });

  it("reproduces Zurich's 2023 National Council members by candidate votes", () => {
    const rules = {
      ...closed,
      order: "open",
      vacancy: "next_by_votes",
    } as const;
    for (const list of zurich.lists) {
      expect(
        fill(rules, list.seats, list.members).elected.toSorted(byText),
        list.list,
      ).toEqual(declared(list.members).toSorted(byText));
    }
  });

  it("reproduces Acre's 2022 federal deputies with the 10% individual threshold", () => {
    const rules = {
      ...closed,
      order: "open",
      individual: 0.1,
      vacancy: "next_by_votes",
    } as const;
    const raw = acre.valid / 8;
    const quotient =
      raw - Math.floor(raw) > 0.5 ? Math.ceil(raw) : Math.floor(raw);
    for (const list of acre.lists) {
      const disqualified = new Set(
        list.members
          .filter((member) => member.disqualified)
          .map(({ id }) => id),
      );
      expect(
        fill(
          rules,
          list.seats,
          list.members,
          disqualified,
          quotient,
        ).elected.toSorted(byText),
        list.list,
      ).toEqual(declared(list.members).toSorted(byText));
    }
  });
});
