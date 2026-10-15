import { describe, expect, it } from "vitest";
import type { Rules } from "./rules.ts";
import italy from "./fixtures/italy-2025-06-08.json" with { type: "json" };
import switzerland from "./fixtures/switzerland-2010-11-28.json" with { type: "json" };
import { decide } from "./referendum.ts";

const swiss: Rules = {
  formula: "referendum",
  doubleMajority: {
    scheme: "ch.canton",
    weights: { OW: 0.5, NW: 0.5, BS: 0.5, BL: 0.5, AR: 0.5, AI: 0.5 },
    types: ["initiative", "mandatory_referendum", "counter_proposal"],
  },
};

describe("referendums", () => {
  it("fails Italy's 2025 abrogative referendums on turnout", () => {
    for (const { question, ...ballot } of italy) {
      expect(
        decide(
          { formula: "referendum", quorum: 0.5 },
          { ...ballot, type: "optional_referendum" },
        ),
        question,
      ).toEqual({ outcome: "quorum_failed" });
    }
  });

  it("decides Switzerland's 28 November 2010 questions by double majority", () => {
    for (const { question, type, won, cantons } of switzerland) {
      let yes = 0;
      let no = 0;
      for (const canton of cantons) {
        yes += canton.yes;
        no += canton.no;
      }
      expect(
        decide(swiss, {
          type: type as "initiative" | "counter_proposal",
          yes,
          no,
          units: cantons,
        }).outcome,
        question,
      ).toBe(won === "Yes" ? "adopted" : "rejected");
    }
  });

  it("needs a majority of cantons, with a tied canton counting as no", () => {
    const cantons = [
      { id: "ZH", yes: 10, no: 10 },
      { id: "BE", yes: 1, no: 3 },
      { id: "OW", yes: 5, no: 1 },
      { id: "NW", yes: 5, no: 1 },
    ];
    expect(
      decide(swiss, { type: "initiative", yes: 21, no: 15, units: cantons }),
    ).toEqual({ outcome: "rejected", units: { yes: 1, no: 2 } });
    expect(
      decide(swiss, {
        type: "optional_referendum",
        yes: 21,
        no: 15,
        units: cantons,
      }),
    ).toEqual({ outcome: "adopted" });
  });
});
