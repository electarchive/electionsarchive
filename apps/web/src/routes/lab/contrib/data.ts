export type Box = readonly [number, number, number, number];

export type Column = 0 | 1;

export const columns = [0, 1] as const;

export type County = {
  id: string;
  name: string;
  isPartial: boolean;
  box: Box;
  votes: readonly [number, number];
  boxes: readonly [Box, Box];
};

export const page = {
  src: "/pages/us.il_sbe/201d435d9f1cff1e6ba83f9cda41a94dc6b54195bfba43e97c020cc523923f4a-39.png",
  width: 555,
  height: 864,
  number: 33,
  sha: "201d435d9f1c",
};

export const contest = {
  title: "2004 U.S. House election in Illinois's 11th congressional district",
  path: "/us/il/2004-11-02-general/house-11",
  date: "2004-11-02",
  dateLabel: "November 2, 2004",
  district: "Illinois's 11th congressional district",
  system: "Plurality, single-member district",
  source: "Illinois State Board of Elections",
  sourceTitle: "Official vote cast at the general election, November 2, 2004",
  sourceUrl: "https://www.elections.il.gov/",
  candidates: [
    {
      id: "weller",
      name: 'Gerald C. "Jerry" Weller',
      party: "Republican",
      abbr: "REP",
      color: "#e81b23",
      incumbent: true,
      total: 173_057,
      totalBox: [0.75, 0.452, 0.815, 0.465] as Box,
      nameBox: [0.118, 0.452, 0.362, 0.465] as Box,
    },
    {
      id: "renner",
      name: "Tari Renner",
      party: "Democratic",
      abbr: "DEM",
      color: "#3333ff",
      incumbent: false,
      total: 121_903,
      totalBox: [0.748, 0.48, 0.815, 0.493] as Box,
      nameBox: [0.118, 0.48, 0.242, 0.493] as Box,
    },
  ],
} as const;

function surname(name: string) {
  return name.split(" ").at(-1) ?? name;
}

export const surnames = [
  surname(contest.candidates[0].name),
  surname(contest.candidates[1].name),
] as const;

function county(
  id: string,
  name: string,
  isPartial: boolean,
  y: readonly [number, number],
  votes: readonly [number, number],
  boxes: readonly [Box, Box],
): County {
  return { id, name, isPartial, box: [0.036, y[0], 0.15, y[1]], votes, boxes };
}

export const counties: Array<County> = [
  county(
    "bureau",
    "Bureau",
    true,
    [0.5671, 0.5768],
    [8695, 4930],
    [
      [0.3357, 0.5671, 0.3772, 0.5768],
      [0.4685, 0.5671, 0.5099, 0.5768],
    ],
  ),
  county(
    "grundy",
    "Grundy",
    false,
    [0.5856, 0.5953],
    [12_147, 7255],
    [
      [0.3273, 0.5856, 0.3766, 0.5953],
      [0.4691, 0.5864, 0.5093, 0.5961],
    ],
  ),
  county(
    "kankakee",
    "Kankakee",
    false,
    [0.6088, 0.6184],
    [26_539, 17_135],
    [
      [0.3261, 0.6088, 0.3772, 0.6184],
      [0.4613, 0.6088, 0.5093, 0.6188],
    ],
  ),
  county(
    "lasalle",
    "LaSalle",
    false,
    [0.6316, 0.6408],
    [30_932, 18_579],
    [
      [0.3261, 0.6316, 0.3772, 0.6408],
      [0.4607, 0.6316, 0.5093, 0.6412],
    ],
  ),
  county(
    "livingston",
    "Livingston",
    true,
    [0.6543, 0.6624],
    [143, 58],
    [
      [0.3508, 0.6543, 0.3772, 0.6624],
      [0.4919, 0.6547, 0.5093, 0.6628],
    ],
  ),
  county(
    "mclean",
    "McLean",
    true,
    [0.6771, 0.6867],
    [14_976, 16_649],
    [
      [0.3273, 0.6771, 0.3772, 0.6867],
      [0.4607, 0.6771, 0.5093, 0.6867],
    ],
  ),
  county(
    "will",
    "Will",
    true,
    [0.6995, 0.7091],
    [78_648, 56_758],
    [
      [0.3261, 0.6995, 0.3766, 0.7091],
      [0.4589, 0.6995, 0.5087, 0.7091],
    ],
  ),
  county(
    "woodford",
    "Woodford",
    true,
    [0.7226, 0.7307],
    [977, 539],
    [
      [0.3496, 0.7226, 0.3766, 0.7307],
      [0.4823, 0.7226, 0.5087, 0.7307],
    ],
  ),
];

export const integer = new Intl.NumberFormat("en-US");

export function share(part: number, whole: number) {
  return whole === 0 ? "0.0" : ((part / whole) * 100).toFixed(1);
}

type Tier = 1 | 2 | 3;

type Change = {
  entity: string;
  field: string;
  before: string | null;
  after: string | null;
  evidence?: number;
};

export type Proposal = {
  number: number;
  title: string;
  sentence: string;
  author: string;
  level: number;
  agent?: { name: string; model: string };
  polity: string;
  tier: Tier;
  kind: string;
  submitted: string;
  age: string;
  revision: number;
  revisions: number;
  prescreen: "clean" | "spam" | "missing_evidence" | "duplicate";
  desk: "approve" | "changes" | "escalated" | "pending";
  deskNote: string;
  checks: { passed: number; findings: number; failed: number };
  touches?: string;
  changes: Array<Change>;
  evidence: Array<{ label: string; detail: string }>;
  table?: boolean;
};

const canvass = {
  label: "Illinois State Board of Elections, Official vote cast, 2004, p. 33",
  detail: "Uploaded PDF, archived Oct 30, SHA-256 201d435d9f1c",
};

export const proposals: [Proposal, ...Array<Proposal>] = [
  {
    number: 1042,
    title: "Add county returns for Illinois's 11th district, 2004",
    sentence:
      "Adds the votes for both candidates in all eight counties of Illinois's 11th district in 2004, read from page 33 of the State Board of Elections' official canvass.",
    author: "ana-k",
    level: 2,
    agent: { name: "canvass-reader", model: "claude-sonnet-5-5" },
    polity: "Illinois",
    tier: 2,
    kind: "Digitization",
    submitted: "Oct 30, 09:12",
    age: "4h",
    revision: 2,
    revisions: 2,
    prescreen: "clean",
    desk: "approve",
    deskNote:
      "Second read agrees on all 16 values; both columns tie to the printed totals.",
    checks: { passed: 7, findings: 0, failed: 0 },
    touches: "us.ahcb county shapes (8 areas, read only)",
    changes: [],
    evidence: [canvass],
    table: true,
  },
  {
    number: 1047,
    title:
      "Add Wikidata IDs for 212 members of Turkey's 28th Grand National Assembly",
    sentence:
      "Links 212 people elected in May 2023 to their Wikidata items, matched on name, province, and party.",
    author: "mert-a",
    level: 0,
    agent: { name: "wikidata-matcher", model: "claude-haiku-5-5" },
    polity: "Turkey",
    tier: 1,
    kind: "External IDs",
    submitted: "Oct 30, 12:40",
    age: "40m",
    revision: 1,
    revisions: 1,
    prescreen: "clean",
    desk: "escalated",
    deskNote:
      "Sent to an editor: the account is at level 0. Spot-checked 30 of 212 matches; all correct.",
    checks: { passed: 4, findings: 0, failed: 0 },
    changes: [
      {
        entity: "Özgür Özel",
        field: "Wikidata",
        before: null,
        after: "Q6082419",
        evidence: 1,
      },
      {
        entity: "Numan Kurtulmuş",
        field: "Wikidata",
        before: null,
        after: "Q470588",
        evidence: 1,
      },
      {
        entity: "Meral Akşener",
        field: "Wikidata",
        before: null,
        after: "Q1349063",
        evidence: 1,
      },
    ],
    evidence: [
      {
        label: "Wikidata",
        detail:
          "Each item's position held: member of the Grand National Assembly, 28th term",
      },
    ],
  },
  {
    number: 1046,
    title: "Merge two records of Hugh Carey",
    sentence:
      "Merges a second record of Hugh L. Carey, created by the 1962 House returns, into the governor's record.",
    author: "jmcbride",
    level: 3,
    polity: "New York",
    tier: 2,
    kind: "Merge",
    submitted: "Oct 29, 18:02",
    age: "19h",
    revision: 1,
    revisions: 1,
    prescreen: "clean",
    desk: "approve",
    deskNote:
      "Same birth date and Brooklyn district in both records; the Biographical Directory lists one person.",
    checks: { passed: 5, findings: 0, failed: 0 },
    touches: "us.clerk (6 candidacies), us.cage (2 candidacies)",
    changes: [
      {
        entity: "Hugh L. Carey (1962)",
        field: "Record",
        before: "pe_01J8Q2…",
        after: "Merged into Hugh Carey",
        evidence: 1,
      },
      {
        entity: "Hugh Carey",
        field: "Candidacies",
        before: "4",
        after: "10",
        evidence: 1,
      },
    ],
    evidence: [
      {
        label: "Biographical Directory of the U.S. Congress, C000149",
        detail: "Archived Oct 29, Wayback and raw copy",
      },
    ],
  },
  {
    number: 1045,
    title: "Set the Greens' color in Germany from 1980",
    sentence:
      "Sets the color of Bündnis 90/Die Grünen to the party's own green from its 1980 founding.",
    author: "lena.w",
    level: 2,
    polity: "Germany",
    tier: 1,
    kind: "Party color",
    submitted: "Oct 30, 11:15",
    age: "2h",
    revision: 1,
    revisions: 1,
    prescreen: "clean",
    desk: "approve",
    deskNote: "Matches the party's published design manual.",
    checks: { passed: 3, findings: 0, failed: 0 },
    changes: [
      {
        entity: "Bündnis 90/Die Grünen",
        field: "Color from 1980",
        before: "#1aa037",
        after: "#46962b",
        evidence: 1,
      },
    ],
    evidence: [
      {
        label: "Bündnis 90/Die Grünen, Corporate Design Manual",
        detail: "Archived Oct 30, p. 12",
      },
    ],
  },
  {
    number: 1044,
    title:
      "Replace Kankakee County's 1996 presidential returns with the county canvass",
    sentence:
      "Publishes the county clerk's canvass as the default for Kankakee County in 1996, replacing the State Board's summary figures.",
    author: "ana-k",
    level: 2,
    polity: "Illinois",
    tier: 3,
    kind: "Correction",
    submitted: "Oct 29, 21:30",
    age: "16h",
    revision: 1,
    revisions: 1,
    prescreen: "clean",
    desk: "pending",
    deskNote: "Pre-review in progress: 3 of 4 figures verified.",
    checks: { passed: 6, findings: 1, failed: 0 },
    touches: "us.il_sbe (1 result set, default)",
    changes: [
      {
        entity: "Kankakee County, Clinton",
        field: "Votes",
        before: "17,431",
        after: "17,413",
        evidence: 1,
      },
      {
        entity: "Kankakee County, Dole",
        field: "Votes",
        before: "19,925",
        after: "19,925",
        evidence: 1,
      },
    ],
    evidence: [
      {
        label: "Kankakee County Clerk, Official canvass 1996",
        detail: "Uploaded scan, p. 4",
      },
    ],
  },
  {
    number: 1043,
    title: "Translate 40 notes on Swiss referendums into German",
    sentence:
      "Adds German translations of 40 English notes on federal referendums from 2010 to 2024.",
    author: "r.baumann",
    level: 1,
    polity: "Switzerland",
    tier: 1,
    kind: "Translation",
    submitted: "Oct 30, 08:05",
    age: "5h",
    revision: 1,
    revisions: 1,
    prescreen: "clean",
    desk: "approve",
    deskNote:
      "Read all 40; terms follow the Federal Chancellery's own German wording.",
    checks: { passed: 2, findings: 0, failed: 0 },
    changes: [
      {
        entity: "Note on the 2020-09-27 referendums",
        field: "German",
        before: null,
        after: "Die Begrenzungsinitiative wurde abgelehnt.",
        evidence: 1,
      },
    ],
    evidence: [
      {
        label: "Federal Chancellery, Erläuterungen des Bundesrates",
        detail: "Archived Oct 30",
      },
    ],
  },
  {
    number: 1039,
    title: "Upload Chicago's 2022 ward boundaries",
    sentence:
      "Adds the 50 wards the City Council adopted in 2022 as a boundary set valid from 2023.",
    author: "pdiaz",
    level: 3,
    polity: "Chicago",
    tier: 2,
    kind: "Boundaries",
    submitted: "Oct 28, 16:44",
    age: "2d",
    revision: 3,
    revisions: 3,
    prescreen: "clean",
    desk: "approve",
    deskNote:
      "Shapes match the city's open data release; no gaps or overlaps above tolerance.",
    checks: { passed: 4, findings: 0, failed: 0 },
    changes: [
      {
        entity: "Ward 1",
        field: "Shape",
        before: null,
        after: "Polygon, 412 points",
        evidence: 1,
      },
    ],
    evidence: [
      {
        label: "City of Chicago, Wards 2023–",
        detail: "GeoJSON, archived Oct 28",
      },
    ],
  },
  {
    number: 1048,
    title: "best election results site click here",
    sentence: "Adds a note to the 2024 presidential contest.",
    author: "new-user-8812",
    level: 0,
    polity: "United States",
    tier: 1,
    kind: "Note",
    submitted: "Oct 30, 13:01",
    age: "12m",
    revision: 1,
    revisions: 1,
    prescreen: "spam",
    desk: "pending",
    deskNote: "Prescreen marked this as spam; hidden from public lists.",
    checks: { passed: 1, findings: 0, failed: 0 },
    changes: [
      {
        entity: "2024 presidential election",
        field: "Note",
        before: null,
        after: "Visit our site…",
      },
    ],
    evidence: [],
  },
];
