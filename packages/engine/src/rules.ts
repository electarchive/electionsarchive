import { toJsonSchema } from "@valibot/to-json-schema";
import * as v from "valibot";

const tieBreak = v.optional(v.picklist(["votes", "lot"]));
const pooling = v.optional(v.picklist(["alliance", "coalition"]));

const threshold = v.object({
  percent: v.pipe(v.number(), v.minValue(0), v.maxValue(100)),
  level: v.picklist(["list", "party", "alliance", "coalition"]),
  scope: v.optional(v.picklist(["contest", "national"]), "contest"),
  constituencies: v.optional(v.pipe(v.number(), v.integer(), v.minValue(1))),
  exempt: v.optional(v.array(v.string())),
});

export const rules = v.variant("formula", [
  v.object({
    formula: v.picklist([
      "dhondt",
      "hagenbach_bischoff",
      "sainte_lague",
      "sainte_lague_schepers",
      "imperiali",
    ]),
    threshold: v.optional(threshold),
    pooling,
    tieBreak,
  }),
  v.object({
    formula: v.literal("modified_sainte_lague"),
    firstDivisor: v.optional(v.pipe(v.number(), v.minValue(1)), 1.4),
    threshold: v.optional(threshold),
    pooling,
    tieBreak,
  }),
  v.object({
    formula: v.picklist(["hare", "droop", "imperiali_quota"]),
    threshold: v.optional(threshold),
    pooling,
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

export const rulesJsonSchema = toJsonSchema(rules);
