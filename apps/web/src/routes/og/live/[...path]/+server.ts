import type { LiveElection, LiveOffice, LiveOption } from "@ea/analytics/live";
import { call, ORPCError } from "@orpc/server";
import { Resvg } from "@resvg/resvg-js";
import { error } from "@sveltejs/kit";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import satori from "satori";
import type { Summary } from "#lib/live/summary.js";
import { count, percent } from "#lib/format.js";
import { stamp, surname } from "#lib/live/format.js";
import { pair } from "#lib/live/standing.js";
import { closing, describe, summarize, tally } from "#lib/live/summary.js";
import { m } from "#lib/paraglide/messages.js";
import { router } from "#lib/server/rpc/router.js";
import type { RequestHandler } from "./$types";

type Node = {
  type: string;
  props: Record<string, unknown> & {
    style?: Record<string, string | number>;
    children?: unknown;
  };
};

const INK = "#15171a";
const MUTED = "#5b616a";
const PAPER = "#ffffff";
const FILL = "#eceef1";
const RULE = "#dadde2";
const MARKER = "#ffe03a";
const OTHER = "#b4b9c0";
const NEUTRALS = ["#8a9099", "#a9aeb6", "#c6cad0"];
const ALERT = "#c2410c";
const WIDTH = 1200;
const HEIGHT = 630;
const INNER = 1072;
const OFFICES: Array<LiveOffice> = ["senate", "governor", "house"];
const MONOGRAM = `data:image/svg+xml;utf8,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="${MARKER}"/><path fill="none" stroke="${INK}" stroke-width="2.4" stroke-linecap="square" d="M9.5 7 V25 M13.75 7 V25 M18 7 V25 M22.25 7 V25 M5.5 21 L26.5 11"/></svg>`,
)}`;

const require = createRequire(import.meta.url);
const fonts = Promise.all(
  (
    [
      ["Piazzolla", "piazzolla", 400],
      ["Sofia", "sofia-sans-semi-condensed", 400],
      ["Sofia", "sofia-sans-semi-condensed", 600],
      ["Sofia", "sofia-sans-semi-condensed", 800],
    ] as const
  ).flatMap(([name, family, weight]) =>
    ["latin", "latin-ext"].map(async (subset) => ({
      name: `${name} ${subset}`,
      weight,
      style: "normal" as const,
      data: await readFile(
        require.resolve(
          `@fontsource/${family}/files/${family}-${subset}-${weight}-normal.woff`,
        ),
      ),
    })),
  ),
);

const h = (
  type: string,
  style: Record<string, string | number>,
  children?: unknown,
): Node => ({ type, props: { style, children } });

function hue(option: LiveOption | null | undefined, index = 0) {
  const color = option?.party?.color ?? "";
  return /^#[\da-f]{6}$/iu.test(color)
    ? color
    : (NEUTRALS[index % NEUTRALS.length] ?? MUTED);
}

function line(text: string, style: Record<string, string | number>) {
  return h(
    "div",
    {
      display: "block",
      overflow: "hidden",
      whiteSpace: "nowrap",
      textOverflow: "ellipsis",
      ...style,
    },
    text,
  );
}

function pill(status: "live" | "before" | "certified") {
  const isLive = status === "live";
  return h(
    "div",
    {
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "6px 16px",
      borderRadius: 999,
      fontSize: 22,
      fontWeight: 600,
      color: isLive ? PAPER : INK,
      backgroundColor: isLive ? INK : PAPER,
      border: isLive ? `2px solid ${INK}` : `2px solid ${RULE}`,
    },
    [
      ...(isLive
        ? [
            h("div", {
              width: 10,
              height: 10,
              borderRadius: 999,
              backgroundColor: ALERT,
            }),
          ]
        : []),
      status === "live"
        ? m.night_status_live()
        : status === "before"
          ? m.night_status_before()
          : m.night_status_certified(),
    ],
  );
}

function meter(share: number | null) {
  return h(
    "div",
    {
      display: "flex",
      flexGrow: 1,
      height: 8,
      borderRadius: 999,
      backgroundColor: FILL,
      overflow: "hidden",
    },
    share == null
      ? []
      : [
          h("div", {
            width: `${Math.min(1, share) * 100}%`,
            height: "100%",
            backgroundColor: INK,
          }),
        ],
  );
}

function side(summary: Summary, index: number, isEnd: boolean) {
  const option = summary.race.options[index];
  const votes = summary.totals[index] ?? 0;
  const isWinner = option != null && summary.winner?.id === option.id;
  return h(
    "div",
    {
      display: "flex",
      flexDirection: "column",
      alignItems: isEnd ? "flex-end" : "flex-start",
      width: 470,
      padding: isEnd ? "0 22px 0 0" : "0 0 0 22px",
      borderLeft: isEnd ? "none" : `5px solid ${hue(option, index)}`,
      borderRight: isEnd ? `5px solid ${hue(option, index)}` : "none",
    },
    [
      line(option?.label ?? "", {
        maxWidth: 440,
        fontSize: 32,
        fontWeight: 800,
        letterSpacing: "-0.01em",
      }),
      line(option?.party?.name ?? "", {
        maxWidth: 440,
        marginTop: 2,
        fontSize: 20,
        color: MUTED,
      }),
      ...(summary.total === 0
        ? []
        : [
            h(
              "div",
              {
                display: "flex",
                alignItems: "baseline",
                flexDirection: isEnd ? "row-reverse" : "row",
                gap: 16,
                marginTop: 10,
              },
              [
                h(
                  "div",
                  {
                    display: "flex",
                    fontSize: 92,
                    fontWeight: 800,
                    lineHeight: 0.9,
                    letterSpacing: "-0.03em",
                    color: INK,
                  },
                  percent(votes / summary.total),
                ),
                h(
                  "div",
                  {
                    display: "flex",
                    flexDirection: "column",
                    alignItems: isEnd ? "flex-end" : "flex-start",
                    fontSize: 22,
                    color: MUTED,
                  },
                  [
                    ...(isWinner
                      ? [
                          h(
                            "div",
                            {
                              display: "flex",
                              padding: "0 8px",
                              backgroundColor: MARKER,
                              color: INK,
                              fontWeight: 800,
                            },
                            m.night_winner(),
                          ),
                        ]
                      : []),
                    m.night_votes({ count: count(votes) }),
                  ],
                ),
              ],
            ),
          ]),
    ],
  );
}

function race(summary: Summary) {
  const [a, b] = pair(summary.race);
  const total = Math.max(1, summary.total);
  const shareA = (summary.totals[a] ?? 0) / total;
  const shareB = (summary.totals[b] ?? 0) / total;
  const rest = summary.total === 0 ? 0 : Math.max(0, 1 - shareA - shareB);
  const chance =
    summary.chance != null && summary.favorite != null
      ? m.night_nation_chance({
          name: surname(summary.favorite),
          chance: percent(summary.chance, 0),
        })
      : null;
  return [
    h("div", { display: "flex", justifyContent: "space-between" }, [
      side(summary, a, false),
      side(summary, b, true),
    ]),
    h(
      "div",
      {
        display: "flex",
        marginTop: 22,
        height: 18,
        backgroundColor: FILL,
        borderRadius: 3,
        overflow: "hidden",
      },
      [
        h("div", {
          width: `${shareA * 100}%`,
          height: "100%",
          backgroundColor: hue(summary.race.options[a], a),
        }),
        h("div", {
          width: `${rest * 100}%`,
          height: "100%",
          backgroundColor: OTHER,
        }),
        h("div", { flexGrow: 1, height: "100%" }),
        h("div", {
          width: `${shareB * 100}%`,
          height: "100%",
          backgroundColor: hue(summary.race.options[b], b),
        }),
      ],
    ),
    h(
      "div",
      {
        display: "flex",
        alignItems: "center",
        gap: 18,
        marginTop: 20,
        fontSize: 22,
        color: MUTED,
      },
      [
        h(
          "div",
          { display: "flex", color: INK, fontWeight: 600 },
          m.night_counted(),
        ),
        meter(summary.counted),
        h(
          "div",
          { display: "flex" },
          summary.counted == null
            ? m.night_counted_none()
            : percent(summary.winner != null ? 1 : summary.counted, 0),
        ),
        ...(chance == null
          ? []
          : [
              h(
                "div",
                {
                  display: "flex",
                  marginLeft: 12,
                  paddingLeft: 18,
                  borderLeft: `1px solid ${RULE}`,
                  color: INK,
                  fontWeight: 600,
                },
                chance,
              ),
            ]),
      ],
    ),
  ];
}

function office(summaries: Array<Summary>, kind: LiveOffice) {
  const { groups, waiting } = tally(summaries, kind);
  const total = summaries.filter((entry) => entry.race.office === kind).length;
  const label =
    kind === "senate"
      ? m.night_nation_senate()
      : kind === "governor"
        ? m.night_nation_governor()
        : m.night_nation_house();
  return h("div", { display: "flex", flexDirection: "column", gap: 10 }, [
    h(
      "div",
      {
        display: "flex",
        alignItems: "baseline",
        gap: 24,
        fontSize: 22,
        color: MUTED,
      },
      [
        h(
          "div",
          {
            display: "flex",
            width: 150,
            fontSize: 28,
            fontWeight: 800,
            color: INK,
          },
          label,
        ),
        ...groups
          .slice(0, 3)
          .map((group, index) =>
            h("div", { display: "flex", alignItems: "center", gap: 10 }, [
              h("div", {
                width: 14,
                height: 14,
                borderRadius: 999,
                backgroundColor: hue(group.option, index),
              }),
              h(
                "div",
                { display: "flex", color: INK, fontWeight: 600 },
                group.option.party?.short ?? surname(group.option),
              ),
              m.night_nation_balance({
                won: count(group.won),
                leading: count(group.leading),
              }),
            ]),
          ),
        h(
          "div",
          { display: "flex", marginLeft: "auto" },
          m.night_og_count({ count: count(total) }),
        ),
      ],
    ),
    h(
      "div",
      {
        display: "flex",
        height: 16,
        backgroundColor: FILL,
        borderRadius: 3,
        overflow: "hidden",
      },
      [
        ...groups.flatMap((group, index) => [
          h("div", {
            width: `${(group.won / total) * 100}%`,
            height: "100%",
            backgroundColor: hue(group.option, index),
          }),
          h("div", {
            width: `${(group.leading / total) * 100}%`,
            height: "100%",
            backgroundColor: hue(group.option, index),
            opacity: 0.45,
          }),
        ]),
        h("div", { width: `${(waiting / total) * 100}%`, height: "100%" }),
      ],
    ),
  ]);
}

function nation(summaries: Array<Summary>, first: number, time: number) {
  const counting = summaries.filter((entry) => entry.total > 0).length;
  const isBefore = counting === 0 && time < first;
  const kinds = OFFICES.filter((kind) =>
    summaries.some((entry) => entry.race.office === kind),
  ).slice(0, 2);
  return {
    isBefore,
    line: isBefore
      ? m.night_og_first({ time: stamp(first) })
      : m.night_og_races({
          counting: count(counting),
          total: count(summaries.length),
        }),
    body: [
      h(
        "div",
        { display: "flex", flexDirection: "column", gap: 26 },
        kinds.map((kind) => office(summaries, kind)),
      ),
    ],
  };
}

async function night(path: string) {
  try {
    return await call(router.live.election, { path });
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      error(404);
    }
    throw error_;
  }
}

function focus(election: LiveElection, isRace: boolean) {
  return isRace
    ? (election.races[0] ?? null)
    : (election.races.find((entry) => entry.office === "senate") ??
        election.races.find((entry) => entry.office === "governor") ??
        election.races[0] ??
        null);
}

export const GET: RequestHandler = async ({ params }) => {
  if (!params.path.endsWith(".png")) {
    error(404);
  }
  const path = params.path.slice(0, -4);
  const first = await night(path);
  const isRace = first.election.slug !== path;
  const { election, estimates } = isRace
    ? await night(first.election.slug)
    : first;
  const isNation = election.slug.split("/").length === 2;
  const time = Date.now();
  let title = election.title;
  let headline: string;
  let body: Array<Node>;
  let status: "live" | "before" | "certified" = election.isCertified
    ? "certified"
    : "live";
  if (isNation) {
    const summaries = election.races.map((entry) =>
      summarize(entry, estimates[entry.id] ?? null),
    );
    const first = Math.min(
      ...election.pollClose.map((entry) => Date.parse(entry.at)),
    );
    const found = nation(summaries, first, time);
    headline = found.line;
    if (found.isBefore) {
      status = "before";
    }
    body = found.body;
  } else {
    const chosen = focus(isRace ? first.election : election, isRace);
    const target = election.races.find((entry) => entry.id === chosen?.id);
    if (target == null) {
      error(404);
    }
    const summary = summarize(target, estimates[target.id] ?? null);
    const closes = closing(election, target);
    title = target.title;
    headline = describe(summary, closes, time);
    body = race(summary);
    if (target.certified != null) {
      status = "certified";
    } else if (summary.total === 0 && time < closes) {
      status = "before";
    }
  }
  const svg = await satori(
    h(
      "div",
      {
        display: "flex",
        flexDirection: "column",
        width: WIDTH,
        height: HEIGHT,
        boxSizing: "border-box",
        padding: "44px 64px 34px",
        backgroundColor: PAPER,
        fontFamily: "Sofia latin, Sofia latin-ext",
        color: INK,
      },
      [
        h(
          "div",
          {
            display: "flex",
            alignItems: "center",
            gap: 18,
            fontSize: 22,
            color: MUTED,
          },
          [
            pill(status),
            election.newest == null
              ? ""
              : m.night_updated({ time: stamp(election.newest) }),
          ],
        ),
        h(
          "div",
          {
            display: "flex",
            width: INNER,
            marginTop: 18,
            fontWeight: 800,
            fontSize: title.length > 48 ? 54 : 66,
            lineHeight: 1,
            letterSpacing: "-0.025em",
          },
          title,
        ),
        h(
          "div",
          {
            display: "flex",
            width: INNER,
            marginTop: 12,
            fontFamily: "Piazzolla latin, Piazzolla latin-ext",
            fontSize: 28,
            lineHeight: 1.25,
            color: MUTED,
          },
          headline,
        ),
        h(
          "div",
          {
            display: "flex",
            flexDirection: "column",
            justifyContent: "flex-end",
            width: INNER,
            flexGrow: 1,
          },
          body,
        ),
        h(
          "div",
          {
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            width: INNER,
            marginTop: 26,
            paddingTop: 20,
            borderTop: `1px solid ${RULE}`,
            fontSize: 24,
          },
          [
            h(
              "div",
              {
                display: "flex",
                alignItems: "center",
                gap: 12,
                fontWeight: 800,
                letterSpacing: "-0.01em",
              },
              [
                {
                  type: "img",
                  props: { src: MONOGRAM, width: 32, height: 32 },
                },
                m.wordmark(),
              ],
            ),
            h(
              "div",
              { display: "flex", color: MUTED, fontSize: 22 },
              election.title === title ? "" : election.title,
            ),
          ],
        ),
      ],
    ) as Parameters<typeof satori>[0],
    { width: WIDTH, height: HEIGHT, fonts: await fonts },
  );
  const png = new Resvg(svg, { fitTo: { mode: "width", value: WIDTH * 2 } })
    .render()
    .asPng();
  return new Response(new Uint8Array(png), {
    headers: {
      "content-type": "image/png",
      "cache-control": election.isCertified
        ? "public, max-age=86400"
        : "public, max-age=60, stale-while-revalidate=60",
    },
  });
};
