import { toJsonSchema } from "@valibot/to-json-schema";
import * as v from "valibot";

const tieBreak = v.optional(v.picklist(["votes", "lot"]));
const pooling = v.optional(v.picklist(["alliance", "coalition"]));
const compensation = v.optional(v.literal("constituency_seats"));

const lists = v.optional(
  v.object({
    order: v.picklist(["closed", "open", "flexible"]),
    quota: v.optional(v.pipe(v.number(), v.minValue(0))),
    individual: v.optional(v.pipe(v.number(), v.minValue(0), v.maxValue(1))),
    constituencies: v.optional(v.picklist(["skip", "all", "covered"])),
    multiple: v.optional(v.picklist(["lowest_share"])),
    exhausted: v.optional(v.picklist(["vacant", "elsewhere"]), "vacant"),
    vacancy: v.picklist([
      "next_on_list",
      "next_by_votes",
      "substitute",
      "by_election",
    ]),
  }),
);

const percent = v.pipe(v.number(), v.minValue(0), v.maxValue(100));
const rounding = v.picklist(["up", "down", "nearest"]);
const seatCount = v.pipe(v.number(), v.integer(), v.minValue(1));

const threshold = v.object({
  percent,
  level: v.picklist(["list", "party", "alliance", "coalition"]),
  scope: v.optional(v.picklist(["contest", "national"]), "contest"),
  constituencies: v.optional(seatCount),
  exempt: v.optional(v.array(v.string())),
  members: v.optional(v.picklist(["any", "coalition", "standalone"])),
  bestLoser: v.optional(v.boolean()),
  requires: v.optional(v.object({ percent })),
});

const thresholds = v.optional(
  v.union([threshold, v.pipe(v.array(threshold), v.minLength(1))]),
);

const bonus = v.optional(
  v.object({
    level: v.optional(v.picklist(["list", "coalition"])),
    share: v.optional(
      v.object({
        percent,
        strict: v.optional(v.boolean()),
        rounds: v.optional(v.array(seatCount)),
      }),
    ),
    size: v.variant("kind", [
      v.object({
        kind: v.literal("fraction"),
        fraction: v.pipe(v.number(), v.gtValue(0), v.maxValue(1)),
        rounding,
        below: v.optional(v.object({ seats: seatCount, rounding })),
      }),
      v.object({ kind: v.literal("seats"), seats: seatCount }),
      v.object({
        kind: v.literal("target"),
        seats: v.optional(seatCount),
        fraction: v.optional(v.pipe(v.number(), v.gtValue(0), v.maxValue(1))),
        rounding: v.optional(rounding),
      }),
    ]),
    remainder: v.optional(v.picklist(["all", "others"])),
  }),
);

export const rules = v.variant("formula", [
  v.object({
    formula: v.picklist([
      "dhondt",
      "hagenbach_bischoff",
      "sainte_lague",
      "sainte_lague_schepers",
      "imperiali",
    ]),
    threshold: thresholds,
    bonus,
    pooling,
    compensation,
    lists,
    tieBreak,
  }),
  v.object({
    formula: v.literal("modified_sainte_lague"),
    firstDivisor: v.optional(v.pipe(v.number(), v.minValue(1)), 1.4),
    threshold: thresholds,
    bonus,
    pooling,
    compensation,
    lists,
    tieBreak,
  }),
  v.object({
    formula: v.picklist(["hare", "droop", "imperiali_quota"]),
    threshold: thresholds,
    bonus,
    pooling,
    remainders: v.optional(v.picklist(["contest", "national"])),
    lists,
    tieBreak,
  }),
  v.object({
    formula: v.picklist([
      "fptp",
      "winner_take_all",
      "block",
      "sntv",
      "limited",
      "cumulative",
      "approval",
    ]),
    tieBreak,
  }),
  v.object({
    formula: v.literal("two_round"),
    majority: v.optional(v.pipe(v.number(), v.minValue(0), v.maxValue(1)), 0.5),
    advance: v.optional(v.pipe(v.number(), v.integer(), v.minValue(2)), 2),
    tieBreak,
  }),
  v.object({
    formula: v.literal("supplementary"),
    majority: v.optional(v.pipe(v.number(), v.minValue(0), v.maxValue(1)), 0.5),
    advance: v.optional(v.pipe(v.number(), v.integer(), v.minValue(2)), 2),
    preferences: v.optional(v.pipe(v.number(), v.integer(), v.minValue(2)), 2),
    tieBreak,
  }),
  v.object({
    formula: v.literal("top_two"),
    advance: v.optional(v.pipe(v.number(), v.integer(), v.minValue(2)), 2),
    tieBreak,
  }),
  v.object({
    formula: v.literal("irv"),
    maxRankings: v.optional(v.pipe(v.number(), v.integer(), v.minValue(1))),
    batchElimination: v.optional(v.boolean(), false),
    tieBreak,
  }),
  v.object({
    formula: v.literal("stv"),
    quota: v.optional(v.picklist(["droop", "hare"]), "droop"),
    surplus: v.optional(
      v.picklist([
        "last_parcel",
        "gregory",
        "weighted_inclusive_gregory",
        "meek",
        "cincinnati",
      ]),
      "gregory",
    ),
    tieBreak,
  }),
  v.object({
    formula: v.literal("repeated_ballot"),
    majorities: v.pipe(
      v.array(
        v.object({
          fromRound: v.pipe(v.number(), v.integer(), v.minValue(1)),
          share: v.pipe(v.number(), v.minValue(0), v.maxValue(1)),
          strict: v.optional(v.boolean(), false),
        }),
      ),
      v.minLength(1),
    ),
    required: v.optional(v.pipe(v.number(), v.integer(), v.minValue(1))),
  }),
  v.object({
    formula: v.literal("caucus"),
    viability: v.pipe(v.number(), v.minValue(0), v.maxValue(1)),
    alignments: v.optional(v.pipe(v.number(), v.integer(), v.minValue(1)), 2),
  }),
  v.object({
    formula: v.literal("referendum"),
    quorum: v.optional(v.pipe(v.number(), v.minValue(0), v.maxValue(1))),
    majority: v.optional(
      v.object({
        share: v.pipe(v.number(), v.minValue(0.5), v.maxValue(1)),
        strict: v.optional(v.boolean(), false),
      }),
    ),
    doubleMajority: v.optional(
      v.object({
        scheme: v.string(),
        weights: v.optional(
          v.record(v.string(), v.pipe(v.number(), v.minValue(0))),
        ),
        types: v.optional(
          v.array(
            v.picklist([
              "initiative",
              "mandatory_referendum",
              "optional_referendum",
              "counter_proposal",
              "plebiscite",
              "recall_question",
            ]),
          ),
        ),
      }),
    ),
  }),
]);

export type Rules = v.InferOutput<typeof rules>;
export type Threshold = v.InferOutput<typeof threshold>;
export type Bonus = NonNullable<v.InferOutput<typeof bonus>>;
export type Lists = NonNullable<v.InferOutput<typeof lists>>;

export const rulesJsonSchema = toJsonSchema(rules);
