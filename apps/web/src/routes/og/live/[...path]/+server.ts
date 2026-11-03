import type { LiveOption, LiveRace } from "@ea/analytics/live";
import { Resvg } from "@resvg/resvg-js";
import { error } from "@sveltejs/kit";
import { Buffer } from "node:buffer";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import satori from "satori";
import type { ChamberView, Control, Family, Seat } from "#lib/live/nation.js";
import type { Summary } from "#lib/live/summary.js";
import { count, percent } from "#lib/format.js";
import { initial, lead, stamp, surname } from "#lib/live/format.js";
import { chamber, seat, sides, STATES } from "#lib/live/nation.js";
import { pair, standing, sum } from "#lib/live/standing.js";
import { closing, describe, summarize } from "#lib/live/summary.js";
import { m } from "#lib/paraglide/messages.js";
import { getLocale } from "#lib/paraglide/runtime.js";
import { remember } from "#lib/server/cache.js";
import { hexes, shapes } from "#lib/server/live-board.js";
import { livePage, night } from "#lib/server/live-page.js";
import type { RequestHandler } from "./$types";

type Node = {
  type: string;
  props: Record<string, unknown> & {
    style?: Record<string, string | number>;
    children?: unknown;
  };
};

type Status = "live" | "before" | "certified";

const INK = "#15171a";
const MUTED = "#5b616a";
const PAPER = "#ffffff";
const FILL = "#eceef1";
const RULE = "#dadde2";
const MARKER = "#ffe03a";
const OTHER = "#b4b9c0";
const NEUTRALS = ["#8a9099", "#a9aeb6", "#c6cad0"];
const ALERT = "#c2410c";
const YES = "#2e7d4f";
const NO = "#c2410c";
const WIDTH = 1200;
const HEIGHT = 630;
const INNER = 1096;
const KEPT = 240;
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

const rendered = new Map<
  string,
  Promise<{ png: Uint8Array; status: Status }>
>();

const h = (
  type: string,
  style: Record<string, string | number>,
  children?: unknown,
): Node => ({ type, props: { style, children } });

function picture(svg: string, width: number, height: number): Node {
  return {
    type: "img",
    props: {
      src: `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`,
      width,
      height,
    },
  };
}

function isHex(color: string | null | undefined): color is string {
  return color != null && /^#[\da-f]{6}$/iu.test(color);
}

function mix(color: string, amount: number) {
  const share = Math.max(0, Math.min(1, amount));
  const channels = [1, 3, 5].map((start) => {
    const value = Number.parseInt(color.slice(start, start + 2), 16);
    return Math.round(value * share + 255 * (1 - share))
      .toString(16)
      .padStart(2, "0");
  });
  return `#${channels.join("")}`;
}

function hue(option: LiveOption | null | undefined, index = 0) {
  const color = option?.party?.color;
  if (isHex(color)) {
    return color;
  }
  if (option?.party == null) {
    if (/^(?:yes|for|approve)\b/iu.test(option?.label ?? "")) {
      return YES;
    }
    if (/^(?:no|against|reject)\b/iu.test(option?.label ?? "")) {
      return NO;
    }
  }
  return NEUTRALS[index % NEUTRALS.length] ?? MUTED;
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

function pill(status: Status) {
  const isLive = status === "live";
  return h(
    "div",
    {
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "5px 15px",
      borderRadius: 999,
      fontSize: 20,
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

function header(status: Status, newest: string | null, context: string) {
  return h(
    "div",
    {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      width: INNER,
    },
    [
      h(
        "div",
        {
          display: "flex",
          alignItems: "center",
          gap: 16,
          fontSize: 20,
          color: MUTED,
        },
        [
          pill(status),
          ...[
            context,
            newest == null ? "" : m.night_updated({ time: stamp(newest) }),
          ]
            .filter((part) => part !== "")
            .map((part) => line(part, { maxWidth: 420 })),
        ],
      ),
      h(
        "div",
        {
          display: "flex",
          alignItems: "center",
          gap: 10,
          fontSize: 22,
          fontWeight: 800,
          letterSpacing: "-0.01em",
        },
        [
          { type: "img", props: { src: MONOGRAM, width: 28, height: 28 } },
          m.wordmark(),
        ],
      ),
    ],
  );
}

function heading(title: string, headline: string, width: number) {
  const size = title.length > 44 ? 44 : title.length > 26 ? 52 : 60;
  return [
    h(
      "div",
      {
        display: "flex",
        width,
        marginTop: 30,
        fontWeight: 800,
        fontSize: size,
        lineHeight: 1,
        letterSpacing: "-0.025em",
      },
      title,
    ),
    h(
      "div",
      {
        display: "flex",
        width,
        marginTop: 14,
        fontFamily: "Piazzolla latin, Piazzolla latin-ext",
        fontSize: 25,
        lineHeight: 1.3,
        color: MUTED,
      },
      headline,
    ),
  ];
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

function segment(amount: number, size: number, color: string) {
  return h("div", {
    width: `${(amount / size) * 100}%`,
    height: "100%",
    backgroundColor: color,
  });
}

function colorOf(control: Control, family: Family) {
  const raw = control.parties[family.id]?.color;
  if (isHex(raw)) {
    return raw;
  }
  return /republican/iu.test(family.name) ? "#c01c28" : "#1a5fb4";
}

function tally(
  view: ChamberView,
  seats: Array<Seat>,
  [left, right]: [Family, Family],
) {
  const held = { left: 0, right: 0 };
  for (const entry of seats) {
    if (entry.side == null || entry.summary.total === 0) {
      if (entry.held === left.id) {
        held.left += 1;
      } else if (entry.held === right.id) {
        held.right += 1;
      }
    }
  }
  const seated = (group: ChamberView["tallies"]["left"]) =>
    group.holdover + group.won + group.projected + group.ahead;
  const isCounting = seats.some((entry) => entry.summary.total > 0);
  return {
    held,
    isCounting,
    other: seated(view.tallies.other),
    left: seated(view.tallies.left) + (isCounting ? 0 : held.left),
    right: seated(view.tallies.right) + (isCounting ? 0 : held.right),
  };
}

function figure(
  value: number,
  color: string,
  isEnd: boolean,
  isCounting: boolean,
) {
  return h(
    "div",
    {
      display: "flex",
      justifyContent: isEnd ? "flex-end" : "flex-start",
      width: 70,
      fontSize: 40,
      fontWeight: 800,
      lineHeight: 1,
      letterSpacing: "-0.02em",
      color: isCounting ? color : mix(color, 0.5),
    },
    count(value),
  );
}

function rowHeader(label: string, detail: string, aside: string) {
  return h(
    "div",
    {
      display: "flex",
      alignItems: "baseline",
      justifyContent: "space-between",
      fontSize: 19,
      color: MUTED,
    },
    [
      h("div", { display: "flex", alignItems: "baseline", gap: 12 }, [
        h(
          "div",
          { display: "flex", fontSize: 26, fontWeight: 800, color: INK },
          label,
        ),
        detail,
      ]),
      aside,
    ],
  );
}

function governorRow(
  view: ChamberView,
  seats: Array<Seat>,
  pairing: [Family, Family],
  [leftColor, rightColor]: [string, string],
) {
  const totals = tally(view, seats, pairing);
  const notUp =
    view.tallies.left.holdover +
    view.tallies.right.holdover +
    view.tallies.other.holdover;
  const ordered = seats.toSorted(
    (a, b) =>
      b.lean - a.lean ||
      a.summary.race.state.localeCompare(b.summary.race.state),
  );
  const tint = (entry: Seat) => {
    const id = entry.summary.total > 0 ? entry.side : entry.held;
    const color =
      id === pairing[0].id
        ? leftColor
        : id === pairing[1].id
          ? rightColor
          : id == null
            ? null
            : OTHER;
    if (color == null) {
      return FILL;
    }
    if (entry.rating === "won" || entry.rating === "projected") {
      return mix(color, entry.rating === "won" ? 1 : 0.85);
    }
    return mix(
      color,
      entry.summary.total > 0
        ? 0.3 + 0.4 * Math.min(1, Math.abs(entry.lean))
        : 0.24,
    );
  };
  const stack = ordered.length > 18 ? 2 : 1;
  const columns = Math.ceil(ordered.length / stack);
  const size = stack === 2 ? 15 : Math.min(26, Math.floor(332 / columns) - 3);
  const tile = (entry: Seat | undefined) =>
    h("div", {
      width: size,
      height: stack === 2 ? size : 26,
      borderRadius: 3,
      backgroundColor: entry == null ? PAPER : tint(entry),
    });
  return h("div", { display: "flex", flexDirection: "column", gap: 8 }, [
    rowHeader(
      m.night_nation_governors(),
      m.night_governors_races({ count: count(seats.length) }),
      m.night_tally_not_up({ count: count(notUp) }),
    ),
    h("div", { display: "flex", alignItems: "center", gap: 14 }, [
      figure(totals.left, leftColor, false, totals.isCounting),
      h(
        "div",
        { display: "flex", flexGrow: 1, justifyContent: "center", gap: 3 },
        Array.from({ length: columns }, (_, column) =>
          h(
            "div",
            { display: "flex", flexDirection: "column", gap: 3 },
            Array.from({ length: stack }, (_, row) =>
              tile(ordered[column * stack + row]),
            ),
          ),
        ),
      ),
      figure(totals.right, rightColor, true, totals.isCounting),
    ]),
  ]);
}

function chamberRow(
  view: ChamberView,
  seats: Array<Seat>,
  label: string,
  pairing: [Family, Family],
  [leftColor, rightColor]: [string, string],
) {
  const { size } = view;
  const { held, other, isCounting, ...totals } = tally(view, seats, pairing);
  return h("div", { display: "flex", flexDirection: "column", gap: 8 }, [
    rowHeader(
      label,
      m.night_state_seats_up({ count: count(seats.length) }),
      m.night_chamber_majority({ count: count(view.majority) }),
    ),
    h("div", { display: "flex", alignItems: "center", gap: 14 }, [
      figure(totals.left, leftColor, false, isCounting),
      h(
        "div",
        { display: "flex", position: "relative", flexGrow: 1, height: 26 },
        [
          h(
            "div",
            {
              display: "flex",
              width: "100%",
              height: "100%",
              backgroundColor: FILL,
              borderRadius: 3,
              overflow: "hidden",
            },
            [
              segment(view.tallies.left.holdover, size, mix(leftColor, 0.62)),
              segment(view.tallies.left.won, size, leftColor),
              segment(view.tallies.left.projected, size, mix(leftColor, 0.85)),
              segment(view.tallies.left.ahead, size, mix(leftColor, 0.42)),
              segment(held.left, size, mix(leftColor, 0.16)),
              h("div", { flexGrow: 1, height: "100%" }),
              segment(other, size, OTHER),
              segment(held.right, size, mix(rightColor, 0.16)),
              segment(view.tallies.right.ahead, size, mix(rightColor, 0.42)),
              segment(
                view.tallies.right.projected,
                size,
                mix(rightColor, 0.85),
              ),
              segment(view.tallies.right.won, size, rightColor),
              segment(view.tallies.right.holdover, size, mix(rightColor, 0.62)),
            ],
          ),
          h("div", {
            position: "absolute",
            left: `${(view.majority / size) * 100}%`,
            top: -6,
            width: 2,
            height: 38,
            marginLeft: -1,
            backgroundColor: INK,
          }),
        ],
      ),
      figure(totals.right, rightColor, true, isCounting),
    ]),
  ]);
}

async function nationBody(
  path: string,
  date: string,
  summaries: Array<Summary>,
  isBefore: boolean,
) {
  const [page, layout] = await Promise.all([livePage(path, null), hexes(date)]);
  const control = page.control;
  const pairing = sides(control);
  if (control == null || pairing == null) {
    return null;
  }
  const colors: [string, string] = [
    colorOf(control, pairing[0]),
    colorOf(control, pairing[1]),
  ];
  const seats = summaries
    .filter((summary) => summary.race.office !== "other")
    .map((summary) => seat(summary, control, pairing));
  const pick = (office: "senate" | "house" | "governor") =>
    seats.filter(
      (entry) =>
        entry.summary.race.office === office &&
        STATES.has(entry.summary.race.state),
    );
  const rows = [
    ...(
      [
        ["senate", m.night_nation_senate(), control.holdovers.senate],
        ["house", m.night_nation_house(), {}],
      ] as const
    ).map(([office, label, holdovers]) =>
      chamberRow(
        chamber(pick(office), office, holdovers, pairing),
        pick(office),
        label,
        pairing,
        colors,
      ),
    ),
    governorRow(
      chamber(
        pick("governor"),
        "governor",
        control.holdovers.governor,
        pairing,
      ),
      pick("governor"),
      pairing,
      colors,
    ),
  ];
  const side = (id: string | null) =>
    id == null
      ? null
      : (control.families[id] ?? id) === pairing[0].id
        ? colors[0]
        : (control.families[id] ?? id) === pairing[1].id
          ? colors[1]
          : OTHER;
  let map: Node | null = null;
  if (layout != null) {
    const fips = new Map(
      Object.entries(layout.labels).map(([code, label]) => [
        label[2].toLowerCase(),
        code,
      ]),
    );
    const fills = new Map<string, string>();
    for (const entry of pick("house")) {
      const { state, district } = entry.summary.race;
      const prefix = fips.get(state);
      if (prefix == null || district == null) {
        continue;
      }
      const number = Number(district);
      const code = `${prefix}${Number.isSafeInteger(number) ? String(number).padStart(2, "0") : district}`;
      const now = side(entry.side);
      const before = side(entry.held);
      let fill = before == null ? FILL : mix(before, 0.2);
      if (now != null && entry.summary.total > 0) {
        fill = mix(
          now,
          entry.rating === "won"
            ? 1
            : entry.rating === "projected"
              ? 0.85
              : 0.3 + 0.4 * Math.min(1, Math.abs(entry.lean)),
        );
      }
      fills.set(code, fill);
    }
    const boxes = Object.values(layout.bounds);
    const west = Math.min(...boxes.map((box) => box[0])) - 1;
    const north = Math.min(...boxes.map((box) => box[1])) - 1;
    const wide = Math.max(...boxes.map((box) => box[2])) + 1 - west;
    const tall = Math.max(...boxes.map((box) => box[3])) + 1 - north;
    const svg = [
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${west} ${north} ${wide} ${tall}">`,
      ...Object.entries(layout.districts).map(
        ([code, d]) =>
          `<path d="${d}" fill="${fills.get(code) ?? FILL}" stroke="${PAPER}" stroke-width="0.18" stroke-linejoin="round"/>`,
      ),
      ...Object.values(layout.states).map(
        (d) =>
          `<path d="${d}" fill="none" stroke="${INK}" stroke-opacity="0.55" stroke-width="0.32" stroke-linejoin="round"/>`,
      ),
      "</svg>",
    ].join("");
    const width = 560;
    const height = Math.round((width * tall) / wide);
    const key = (color: string, text: string) =>
      h("div", { display: "flex", alignItems: "center", gap: 8 }, [
        h("div", {
          width: 14,
          height: 14,
          borderRadius: 2,
          backgroundColor: color,
        }),
        text,
      ]);
    map = h(
      "div",
      { display: "flex", flexDirection: "column", alignItems: "flex-end" },
      [
        picture(svg, width, height),
        h(
          "div",
          {
            display: "flex",
            gap: 20,
            marginTop: 10,
            fontSize: 18,
            color: MUTED,
          },
          isBefore
            ? [
                key(
                  mix(colors[0], 0.2),
                  m.night_state_held({ party: pairing[0].name }),
                ),
                key(
                  mix(colors[1], 0.2),
                  m.night_state_held({ party: pairing[1].name }),
                ),
              ]
            : [
                key(colors[0], pairing[0].short),
                key(colors[1], pairing[1].short),
                m.night_house_map(),
              ],
        ),
      ],
    );
  }
  return { rows, map };
}

function candidate(
  race: LiveRace,
  summary: Summary,
  index: number,
  prior: Array<number>,
) {
  const option = race.options[index];
  const color = hue(option, index);
  const isCounting = summary.total > 0;
  const priorTotal = sum(prior);
  const share = isCounting
    ? (summary.totals[index] ?? 0) / summary.total
    : priorTotal > 0
      ? (prior[index] ?? 0) / priorTotal
      : null;
  const isWinner = option != null && summary.winner?.id === option.id;
  const tag = (text: string, background: string, border: string) =>
    h(
      "div",
      {
        display: "flex",
        padding: "1px 8px",
        fontSize: 17,
        fontWeight: 800,
        color: INK,
        backgroundColor: background,
        border: `1.5px solid ${border}`,
        borderRadius: 3,
      },
      text,
    );
  return h(
    "div",
    {
      display: "flex",
      alignItems: "center",
      gap: 20,
      paddingLeft: 20,
      borderLeft: `7px solid ${color}`,
    },
    [
      h(
        "div",
        { display: "flex", flexDirection: "column", flexGrow: 1, width: 470 },
        [
          h("div", { display: "flex", alignItems: "center", gap: 12 }, [
            line(option?.label ?? "", {
              maxWidth: isWinner || option?.isIncumbent === true ? 330 : 470,
              fontSize: 36,
              fontWeight: 800,
              letterSpacing: "-0.015em",
            }),
            ...(isWinner ? [tag(m.night_winner(), MARKER, MARKER)] : []),
            ...(option?.isIncumbent === true && !isWinner
              ? [tag(m.results_incumbent(), PAPER, RULE)]
              : []),
          ]),
          line(
            option?.party?.name ??
              (isCounting
                ? m.night_votes({ count: count(summary.totals[index] ?? 0) })
                : ""),
            { maxWidth: 380, marginTop: 2, fontSize: 21, color: MUTED },
          ),
        ],
      ),
      ...(share == null
        ? []
        : [
            h(
              "div",
              {
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-end",
              },
              [
                h(
                  "div",
                  {
                    display: "flex",
                    fontSize: isCounting ? 62 : 40,
                    fontWeight: 800,
                    lineHeight: 0.95,
                    letterSpacing: "-0.03em",
                    color: isCounting ? INK : OTHER,
                  },
                  percent(share),
                ),
                h(
                  "div",
                  { display: "flex", fontSize: 18, color: MUTED },
                  isCounting
                    ? option?.party == null
                      ? ""
                      : m.night_votes({
                          count: count(summary.totals[index] ?? 0),
                        })
                    : m.night_table_base(),
                ),
              ],
            ),
          ]),
    ],
  );
}

function raceBody(summary: Summary, width: number) {
  const { race } = summary;
  const [a, b] = pair(race);
  const shown = race.options.length > 1 ? [a, b] : [a];
  const prior = race.options.map((_, index) =>
    sum(race.baseline.map((row) => (row.shares[index] ?? 0) * row.turnout)),
  );
  const isCounting = summary.total > 0;
  const base = isCounting ? summary.totals : prior;
  const total = Math.max(1, sum(base));
  const shareA = (base[a] ?? 0) / total;
  const shareB = shown.length > 1 ? (base[b] ?? 0) / total : 0;
  const rest = sum(base) === 0 ? 0 : Math.max(0, 1 - shareA - shareB);
  const tint = (color: string) => (isCounting ? color : mix(color, 0.35));
  const order = prior
    .map((votes, index) => ({ votes, option: race.options[index] }))
    .toSorted((x, y) => y.votes - x.votes);
  const priorTotal = sum(prior);
  const top = order[0];
  const baseline =
    priorTotal > 0 && top?.option != null
      ? m.night_tip_baseline({
          margin: `${top.option.party == null ? surname(top.option) : initial(top.option)}+${lead((top.votes - (order[1]?.votes ?? 0)) / priorTotal)}`,
        })
      : null;
  const chance =
    summary.chance != null && summary.favorite != null
      ? m.night_nation_chance({
          name: surname(summary.favorite),
          chance: percent(summary.chance, 0),
        })
      : null;
  return h(
    "div",
    { display: "flex", flexDirection: "column", width, gap: 22 },
    [
      ...shown.map((index) => candidate(race, summary, index, prior)),
      h(
        "div",
        {
          display: "flex",
          height: 16,
          backgroundColor: FILL,
          borderRadius: 3,
          overflow: "hidden",
        },
        sum(base) === 0
          ? []
          : [
              h("div", {
                width: `${shareA * 100}%`,
                height: "100%",
                backgroundColor: tint(hue(race.options[a], a)),
              }),
              h("div", {
                width: `${rest * 100}%`,
                height: "100%",
                backgroundColor: tint(OTHER),
              }),
              h("div", { flexGrow: 1, height: "100%" }),
              h("div", {
                width: `${shareB * 100}%`,
                height: "100%",
                backgroundColor: tint(hue(race.options[b], b)),
              }),
            ],
      ),
      h(
        "div",
        {
          display: "flex",
          alignItems: "center",
          gap: 16,
          fontSize: 20,
          color: MUTED,
        },
        summary.winner != null || (isCounting && summary.counted != null)
          ? [
              h(
                "div",
                { display: "flex", color: INK, fontWeight: 600 },
                m.night_counted(),
              ),
              meter(summary.winner != null ? 1 : summary.counted),
              h(
                "div",
                { display: "flex" },
                percent(summary.winner != null ? 1 : (summary.counted ?? 0), 0),
              ),
              ...(chance == null || summary.winner != null
                ? []
                : [
                    h(
                      "div",
                      {
                        display: "flex",
                        marginLeft: 8,
                        paddingLeft: 16,
                        borderLeft: `1px solid ${RULE}`,
                        color: INK,
                        fontWeight: 600,
                      },
                      chance,
                    ),
                  ]),
            ]
          : isCounting
            ? []
            : [
                h(
                  "div",
                  { display: "flex", color: INK, fontWeight: 600 },
                  m.night_counted_none(),
                ),
                ...(baseline == null
                  ? []
                  : [
                      h(
                        "div",
                        {
                          display: "flex",
                          paddingLeft: 16,
                          borderLeft: `1px solid ${RULE}`,
                        },
                        baseline,
                      ),
                    ]),
              ],
      ),
    ],
  );
}

async function raceMap(summary: Summary, date: string) {
  const { race } = summary;
  const reported = new Set(race.counts.map((entry) => entry.unitId));
  const scheme =
    race.units.find((unit) => reported.has(unit.id))?.scheme ??
    race.units[0]?.scheme;
  const areas = [
    ...new Set(
      race.units.flatMap((unit) =>
        unit.areaId == null || unit.scheme !== scheme ? [] : [unit.areaId],
      ),
    ),
  ];
  if (areas.length < 2) {
    return null;
  }
  const clip = race.office === "house" ? (race.area ?? null) : null;
  const drawn = await remember(
    `live:og:shapes:v1:${race.slug}:${scheme ?? ""}`,
    async () => shapes(areas, date, clip).catch(() => null),
  );
  if (drawn == null || drawn.paths.length === 0) {
    return null;
  }
  const rows = [...standing(race, { at: null, isFinal: true }).units.values()];
  const isCounting = rows.some((row) => row.leader != null);
  const [a, b] = pair(race);
  const fills = new Map<string, string>();
  for (const row of rows) {
    const areaId = row.unit.areaId;
    if (areaId == null) {
      continue;
    }
    if (isCounting && row.leader != null) {
      fills.set(
        areaId,
        mix(
          hue(race.options[row.leader], row.leader),
          0.3 + 0.7 * Math.min(1, row.margin / 0.4),
        ),
      );
    } else if (!isCounting && row.baseSigned != null) {
      const index = row.baseSigned >= 0 ? a : b;
      fills.set(
        areaId,
        mix(
          hue(race.options[index], index),
          0.14 + 0.62 * Math.min(1, Math.abs(row.baseSigned) / 0.5),
        ),
      );
    }
  }
  const stroke = Math.max(1, Math.max(drawn.width, drawn.height) / 420);
  const svg = [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${-stroke} ${-stroke} ${drawn.width + 2 * stroke} ${drawn.height + 2 * stroke}">`,
    ...drawn.paths.map(
      (path) =>
        `<path d="${path.d}" fill="${fills.get(path.areaId) ?? FILL}" stroke="${PAPER}" stroke-width="${stroke}" stroke-linejoin="round"/>`,
    ),
    "</svg>",
  ].join("");
  const fit = Math.min(
    400 / (drawn.width + 2 * stroke),
    390 / (drawn.height + 2 * stroke),
  );
  return h(
    "div",
    {
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      width: 420,
      flexGrow: 1,
    },
    [
      picture(
        svg,
        Math.round((drawn.width + 2 * stroke) * fit),
        Math.round((drawn.height + 2 * stroke) * fit),
      ),
      h(
        "div",
        { display: "flex", marginTop: 12, fontSize: 18, color: MUTED },
        isCounting ? m.night_map_lead() : m.night_table_base(),
      ),
    ],
  );
}

async function card(path: string, bucket: number) {
  const first = await night(path);
  const isRace = first.election.slug !== path;
  const { election, estimates } = isRace
    ? await night(first.election.slug)
    : first;
  const isNation = election.slug.split("/").length === 2;
  const time = bucket;
  let status: Status = election.isCertified ? "certified" : "live";
  let page: Node;
  if (isNation) {
    const summaries = election.races.map((entry) =>
      summarize(entry, estimates[entry.id] ?? null),
    );
    const opens = Math.min(
      ...election.pollClose.map((entry) => Date.parse(entry.at)),
    );
    const counting = summaries.filter((entry) => entry.total > 0).length;
    const isBefore = counting === 0 && time < opens;
    if (isBefore) {
      status = "before";
    }
    const headline = isBefore
      ? m.night_og_first({ time: stamp(opens) })
      : m.night_og_races({
          counting: count(counting),
          total: count(summaries.length),
        });
    const body = await nationBody(
      election.slug,
      election.date,
      summaries,
      isBefore,
    );
    page = h(
      "div",
      { display: "flex", justifyContent: "space-between", width: INNER },
      [
        h("div", { display: "flex", flexDirection: "column", width: 500 }, [
          ...heading(election.title, headline, 500),
          h(
            "div",
            {
              display: "flex",
              flexDirection: "column",
              gap: 22,
              marginTop: 30,
            },
            body?.rows ?? [],
          ),
        ]),
        ...(body?.map == null
          ? []
          : [
              h(
                "div",
                {
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "flex-end",
                  paddingTop: 22,
                },
                [body.map],
              ),
            ]),
      ],
    );
  } else {
    const chosen = isRace
      ? first.election.races[0]
      : (election.races.find((entry) => entry.office === "senate") ??
        election.races.find((entry) => entry.office === "governor") ??
        election.races[0]);
    const target = election.races.find((entry) => entry.id === chosen?.id);
    if (target == null) {
      error(404);
    }
    const summary = summarize(target, estimates[target.id] ?? null);
    const closes = closing(election, target);
    if (target.certified != null) {
      status = "certified";
    } else if (summary.total === 0 && time < closes) {
      status = "before";
    }
    const map = await raceMap(summary, election.date);
    const width = map == null ? INNER : 640;
    page = h(
      "div",
      { display: "flex", justifyContent: "space-between", width: INNER },
      [
        h("div", { display: "flex", flexDirection: "column", width }, [
          ...heading(target.title, describe(summary, closes, time), width),
          h(
            "div",
            {
              display: "flex",
              flexDirection: "column",
              justifyContent: "flex-end",
              flexGrow: 1,
            },
            [raceBody(summary, width)],
          ),
        ]),
        ...(map == null ? [] : [map]),
      ],
    );
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
        padding: "36px 52px 44px",
        backgroundColor: PAPER,
        fontFamily: "Sofia latin, Sofia latin-ext",
        color: INK,
      },
      [
        header(
          status,
          election.newest,
          isNation || election.title === path ? "" : election.title,
        ),
        h("div", { display: "flex", flexGrow: 1, width: INNER }, [page]),
      ],
    ) as Parameters<typeof satori>[0],
    { width: WIDTH, height: HEIGHT, fonts: await fonts },
  );
  const png = new Resvg(svg, {
    fitTo: { mode: "width", value: WIDTH * 2 },
    font: { loadSystemFonts: false },
  })
    .render()
    .asPng();
  return { png: new Uint8Array(png), status };
}

export const GET: RequestHandler = async ({ params }) => {
  if (!params.path.endsWith(".png")) {
    error(404);
  }
  const path = params.path.slice(0, -4);
  const { election } = await night(path);
  const now = Date.now();
  const bucket = now - (now % 60_000);
  const key = `${getLocale()}|${path}|${election.newest ?? ""}|${election.isCertified}|${bucket}`;
  let found = rendered.get(key);
  if (found == null) {
    found = card(path, bucket);
    rendered.set(key, found);
    found.catch(() => rendered.delete(key));
    while (rendered.size > KEPT) {
      const oldest = rendered.keys().next().value;
      if (oldest == null) {
        break;
      }
      rendered.delete(oldest);
    }
  }
  const { png, status } = await found;
  return respond(png, status);
};

function respond(png: Uint8Array, status: Status) {
  return new Response(new Uint8Array(png), {
    headers: {
      "content-type": "image/png",
      "cache-control":
        status === "certified"
          ? "public, max-age=86400"
          : status === "before"
            ? "public, max-age=300, stale-while-revalidate=300"
            : "public, max-age=60, stale-while-revalidate=60",
    },
  });
}
