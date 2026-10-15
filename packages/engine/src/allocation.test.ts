import { describe, expect, it } from "vitest";
import { allocate, qualify } from "./allocation.ts";
import germany from "./fixtures/germany-2025.json" with { type: "json" };
import germanyEuropean from "./fixtures/germany-ep-2024.json" with { type: "json" };
import turkey2007 from "./fixtures/turkey-2007.json" with { type: "json" };
import turkey2011 from "./fixtures/turkey-2011.json" with { type: "json" };
import turkeyJune2015 from "./fixtures/turkey-2015-06.json" with { type: "json" };
import turkeyNovember2015 from "./fixtures/turkey-2015-11.json" with { type: "json" };
import turkey2018 from "./fixtures/turkey-2018.json" with { type: "json" };
import turkey from "./fixtures/turkey-2023.json" with { type: "json" };

const textbook = [
  { id: "A", votes: 100_000 },
  { id: "B", votes: 80_000 },
  { id: "C", votes: 30_000 },
  { id: "D", votes: 20_000 },
];
const remainders = [
  { id: "A", votes: 47_000 },
  { id: "B", votes: 16_000 },
  { id: "C", votes: 15_800 },
  { id: "D", votes: 12_000 },
  { id: "E", votes: 6100 },
  { id: "F", votes: 3100 },
];

describe("divisor methods", () => {
  it("allocates D'Hondt", () => {
    expect(allocate({ formula: "dhondt" }, 8, textbook).seats).toEqual({
      A: 4,
      B: 3,
      C: 1,
      D: 0,
    });
  });

  it("treats Hagenbach-Bischoff as D'Hondt", () => {
    expect(allocate({ formula: "hagenbach_bischoff" }, 8, textbook)).toEqual(
      allocate({ formula: "dhondt" }, 8, textbook),
    );
  });

  it("allocates Sainte-Laguë", () => {
    expect(allocate({ formula: "sainte_lague" }, 8, textbook).seats).toEqual({
      A: 3,
      B: 3,
      C: 1,
      D: 1,
    });
  });

  it("allocates modified Sainte-Laguë", () => {
    expect(
      allocate(
        { formula: "modified_sainte_lague", firstDivisor: 1.4 },
        8,
        textbook,
      ),
    ).toEqual({
      seats: { A: 3, B: 3, C: 1, D: 0 },
      tie: { ids: ["A", "D"], seats: 1 },
    });
  });

  it("allocates Imperiali divisors", () => {
    expect(allocate({ formula: "imperiali" }, 8, textbook).seats).toEqual({
      A: 5,
      B: 3,
      C: 0,
      D: 0,
    });
  });

  it("reports a tie for the last seat", () => {
    const entries = [
      { id: "X", votes: 100 },
      { id: "Y", votes: 50 },
      { id: "Z", votes: 50 },
    ];
    expect(allocate({ formula: "dhondt" }, 2, entries)).toEqual({
      seats: { X: 1, Y: 0, Z: 0 },
      tie: { ids: ["X", "Y", "Z"], seats: 1 },
    });
    expect(
      allocate({ formula: "dhondt", tieBreak: "votes" }, 2, entries),
    ).toEqual({ seats: { X: 2, Y: 0, Z: 0 } });
  });
});

describe("largest remainder", () => {
  it("allocates with the Hare quota", () => {
    expect(allocate({ formula: "hare" }, 10, remainders).seats).toEqual({
      A: 5,
      B: 2,
      C: 1,
      D: 1,
      E: 1,
      F: 0,
    });
  });

  it("allocates with the Droop quota", () => {
    expect(allocate({ formula: "droop" }, 10, remainders).seats).toEqual({
      A: 5,
      B: 2,
      C: 2,
      D: 1,
      E: 0,
      F: 0,
    });
  });

  it("reports a tie for the last remainder seat", () => {
    const entries = [
      { id: "X", votes: 3 },
      { id: "Y", votes: 1 },
      { id: "Z", votes: 1 },
    ];
    expect(allocate({ formula: "hare" }, 2, entries)).toEqual({
      seats: { X: 1, Y: 0, Z: 0 },
      tie: { ids: ["Y", "Z"], seats: 1 },
    });
  });
});

describe("plurality", () => {
  it("elects the top candidates of a block vote", () => {
    const entries = [
      { id: "A", votes: 900 },
      { id: "B", votes: 700 },
      { id: "C", votes: 650 },
      { id: "D", votes: 200 },
    ];
    expect(allocate({ formula: "block" }, 3, entries).seats).toEqual({
      A: 1,
      B: 1,
      C: 1,
      D: 0,
    });
    expect(allocate({ formula: "fptp" }, 1, entries).seats).toEqual({
      A: 1,
      B: 0,
      C: 0,
      D: 0,
    });
  });

  it("gives every seat of a slate to the plurality winner", () => {
    const entries = [
      { id: "A", votes: 900 },
      { id: "B", votes: 700 },
    ];
    expect(allocate({ formula: "winner_take_all" }, 10, entries).seats).toEqual(
      { A: 10, B: 0 },
    );
    expect(
      allocate({ formula: "winner_take_all" }, 10, [
        { id: "A", votes: 5 },
        { id: "B", votes: 5 },
      ]),
    ).toEqual({ seats: { A: 0, B: 0 }, tie: { ids: ["A", "B"], seats: 10 } });
  });
});

describe("golden elections", () => {
  for (const { election, parliament, rules, threshold } of [
    {
      election: "2023",
      parliament: turkey,
      rules: { formula: "dhondt" },
      threshold: { percent: 7, level: "alliance", scope: "national" },
    },
    {
      election: "2018",
      parliament: turkey2018,
      rules: { formula: "dhondt", pooling: "alliance" },
      threshold: { percent: 10, level: "alliance", scope: "national" },
    },
    {
      election: "November 2015",
      parliament: turkeyNovember2015,
      rules: { formula: "dhondt" },
      threshold: { percent: 10, level: "party", scope: "national" },
    },
    {
      election: "June 2015",
      parliament: turkeyJune2015,
      rules: { formula: "dhondt" },
      threshold: { percent: 10, level: "party", scope: "national" },
    },
    {
      election: "2011",
      parliament: turkey2011,
      rules: { formula: "dhondt" },
      threshold: { percent: 10, level: "party", scope: "national" },
    },
    {
      election: "2007",
      parliament: turkey2007,
      rules: { formula: "dhondt" },
      threshold: { percent: 10, level: "party", scope: "national" },
    },
  ] as const) {
    it(`reproduces Turkey ${election} in all ${parliament.districts.length} districts`, () => {
      const passed = qualify(
        threshold,
        parliament.districts.flatMap(({ lists }) => lists),
        parliament.valid,
      );
      for (const { name, seats, independents, lists } of parliament.districts) {
        const allocation = allocate(
          rules,
          seats - independents,
          lists.filter(({ group }) => passed.has(group)),
        );
        expect(allocation.tie, name).toBeUndefined();
        for (const { id, declared } of lists)
          expect(allocation.seats[id] ?? 0, `${name}: ${id}`).toBe(declared);
      }
    });
  }

  it("reproduces the 2025 Bundestag allocation", () => {
    const passed = qualify(
      {
        percent: 5,
        level: "party",
        scope: "national",
        constituencies: 3,
        exempt: ["SSW"],
      },
      germany.parties,
      germany.valid,
    );
    const allocation = allocate(
      { formula: "sainte_lague_schepers" },
      germany.seats,
      germany.parties.filter(({ id }) => passed.has(id)),
    );
    for (const { id, declared } of germany.parties)
      expect(allocation.seats[id] ?? 0, id).toBe(declared);
  });

  it("reproduces Germany's 2024 European Parliament allocation", () => {
    const allocation = allocate(
      { formula: "sainte_lague_schepers" },
      germanyEuropean.seats,
      germanyEuropean.lists,
    );
    for (const { id, declared } of germanyEuropean.lists)
      expect(allocation.seats[id], id).toBe(declared);
  });
});
