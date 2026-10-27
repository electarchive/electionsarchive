import * as v from "valibot";
import { describe, expect, it } from "vitest";
import { apportion } from "./allocation.ts";
import chenove from "./fixtures/france-2026-chenove.json" with { type: "json" };
import paris from "./fixtures/france-2026-paris.json" with { type: "json" };
import paris18 from "./fixtures/france-2026-paris-18.json" with { type: "json" };
import saulxures from "./fixtures/france-2026-saulxures-les-nancy.json" with { type: "json" };
import surgeres from "./fixtures/france-2026-surgeres.json" with { type: "json" };
import camera2006 from "./fixtures/italy-camera-2006.json" with { type: "json" };
import camera2008 from "./fixtures/italy-camera-2008.json" with { type: "json" };
import camera2013 from "./fixtures/italy-camera-2013.json" with { type: "json" };
import lombardia from "./fixtures/italy-senate-2006-lombardia.json" with { type: "json" };
import { rules } from "./rules.ts";

type Fixture = {
  seats: number;
  round: number;
  valid: number | null;
  rules: unknown;
  entries: Array<{ id: string; votes: number; group?: string }>;
  declared: Record<string, number>;
};

function run(fixture: Fixture) {
  const parsed = v.parse(rules, fixture.rules);
  if (!("threshold" in parsed)) throw new Error("not a proportional rule");
  return apportion(parsed, fixture.seats, fixture.entries, {
    valid: fixture.valid ?? undefined,
    round: fixture.round,
  });
}

const half: Parameters<typeof apportion>[0] = {
  formula: "dhondt",
  tieBreak: "votes",
  bonus: {
    share: { percent: 50, strict: true, rounds: [1] },
    size: {
      kind: "fraction",
      fraction: 0.5,
      rounding: "up",
      below: { seats: 4, rounding: "down" },
    },
  },
};

describe("majority bonuses", () => {
  for (const [name, fixture] of [
    ["Italy's Camera 2006", camera2006],
    ["Italy's Camera 2008", camera2008],
    ["Italy's Camera 2013", camera2013],
    ["Italy's Senate 2006 in Lombardy", lombardia],
    ["Chenôve 2026, a first-round majority", chenove],
    ["Surgères 2026, a second round", surgeres],
    ["Paris 2026, a quarter of the Council of Paris", paris],
    ["Paris 2026, the 18th arrondissement's council", paris18],
  ] as const) {
    it(`reproduces ${name}`, () => {
      expect(run(fixture).seats).toEqual(fixture.declared);
    });
  }

  it("gives no bonus to a first-round leader just short of a majority", () => {
    const result = run(saulxures);
    expect(
      Object.values(saulxures.declared).every((seats) => seats === 0),
    ).toBe(true);
    expect(result.leader).toBeUndefined();
    expect(result.bonus).toBe(0);
  });

  it("rounds the half down below four seats and up above", () => {
    const entries = [
      { id: "A", votes: 600 },
      { id: "B", votes: 400 },
    ];
    expect(apportion(half, 3, entries)).toMatchObject({
      bonus: 1,
      seats: { A: 2, B: 1 },
    });
    expect(apportion(half, 4, entries)).toMatchObject({
      bonus: 2,
      seats: { A: 3, B: 1 },
    });
    expect(apportion(half, 7, entries)).toMatchObject({
      bonus: 4,
      seats: { A: 6, B: 1 },
    });
  });

  it("adds a fixed number of seats and shares the rest", () => {
    expect(
      apportion(
        { formula: "dhondt", bonus: { size: { kind: "seats", seats: 4 } } },
        10,
        [
          { id: "A", votes: 500 },
          { id: "B", votes: 500 - 1 },
        ],
      ),
    ).toMatchObject({ bonus: 4, seats: { A: 7, B: 3 } });
  });

  it("tops up to a target only when the leader falls short", () => {
    const target = {
      formula: "hare",
      bonus: {
        size: { kind: "target", fraction: 0.55, rounding: "up" },
        remainder: "others",
      },
    } as const;
    expect(
      apportion(target, 10, [
        { id: "A", votes: 45 },
        { id: "B", votes: 35 },
        { id: "C", votes: 20 },
      ]),
    ).toMatchObject({ bonus: 2, seats: { A: 6, B: 3, C: 1 } });
    expect(
      apportion(target, 10, [
        { id: "A", votes: 70 },
        { id: "B", votes: 30 },
      ]),
    ).toMatchObject({ bonus: 0, seats: { A: 7, B: 3 } });
  });
});
