import { openapi } from "@orpc/openapi";
import { ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { cells, editable, roles } from "#lib/server/contrib.js";
import { db } from "#lib/server/db/index.js";
import { keyed, reader, run, WRITES } from "#lib/server/rpc/keyed.js";

const PRECISION = ["day", "month", "year", "unknown"];

const dated = {
  valid_from: { type: "date", required: false },
  valid_from_precision: { type: "enum", values: PRECISION, required: false },
  valid_to: { type: "date", required: false },
  valid_to_precision: { type: "enum", values: PRECISION, required: false },
};

const TABLES: Record<
  string,
  {
    ops: Array<string>;
    tier: number | string;
    evidence: string;
    key?: Array<string>;
    columns: Record<
      string,
      { type: string; values?: Array<string>; required: boolean }
    >;
  }
> = {
  name: {
    ops: ["add", "set", "drop"],
    tier: 1,
    evidence: "required",
    columns: {
      entity_id: { type: "uuid", required: true },
      text: { type: "text", required: true },
      lang: { type: "bcp47", required: true },
      script: { type: "text", required: false },
      kind: {
        type: "enum",
        values: [
          "official",
          "native",
          "english",
          "historical",
          "transliteration",
          "abbreviation",
          "ballot",
          "short",
        ],
        required: true,
      },
      ...dated,
      is_preferred: { type: "boolean", required: false },
    },
  },
  note: {
    ops: ["add", "set", "drop"],
    tier: 1,
    evidence: "required when the note states a fact",
    columns: {
      entity_id: { type: "uuid", required: true },
      kind: {
        type: "enum",
        values: ["context", "caveat", "methodology", "editorial", "retired"],
        required: true,
      },
      body_md: { type: "markdown, up to 10,000 characters", required: true },
      lang: { type: "bcp47", required: true },
      ...dated,
    },
  },
  external_id: {
    ops: ["add", "drop"],
    tier: 1,
    evidence: "optional",
    key: ["scheme", "value"],
    columns: {
      entity_id: { type: "uuid", required: true },
      scheme: {
        type: "enum",
        values: ["wikidata", "bioguide", "mnis", "parlgov"],
        required: true,
      },
      value: {
        type: "text: Q123 for wikidata, A000123 for bioguide, digits for mnis and parlgov",
        required: true,
      },
    },
  },
  party_color: {
    ops: ["add", "set", "end", "drop"],
    tier: 1,
    evidence: "required",
    key: ["party_id", "valid_from"],
    columns: {
      party_id: { type: "uuid", required: true },
      color: { type: "hex, like #1a2b3c", required: true },
      valid_from: { type: "date", required: false },
      valid_to: { type: "date", required: false },
    },
  },
  party_image: {
    ops: ["image", "drop"],
    tier: "1 for Wikimedia Commons files under a free license",
    evidence: "optional",
    columns: {
      party_id: { type: "uuid", required: true },
      file: {
        type: "Wikimedia Commons file name or file page URL",
        required: true,
      },
      valid_from: { type: "date", required: false },
      valid_to: { type: "date", required: false },
    },
  },
  person_image: {
    ops: ["image", "drop"],
    tier: "1 for Wikimedia Commons files under a free license",
    evidence: "optional",
    columns: {
      person_id: { type: "uuid", required: true },
      file: {
        type: "Wikimedia Commons file name or file page URL",
        required: true,
      },
    },
  },
  translation: {
    ops: ["translate", "drop"],
    tier: 1,
    evidence: "required only when origin is official",
    columns: {},
  },
  citation: {
    ops: ["cite", "drop"],
    tier: 1,
    evidence: "required",
    columns: {},
  },
  label: {
    ops: ["label", "drop"],
    tier: 1,
    evidence: "required",
    columns: {
      entity_id: { type: "uuid", required: true },
      kind: {
        type: "enum",
        values: ["ballot", "integrity", "recognition", "data_quality"],
        required: true,
      },
      value: { type: "text, up to 200 characters", required: true },
      assessor: {
        type: "text, up to 200 characters; required for integrity and recognition",
        required: false,
      },
      valid_from: { type: "date", required: false },
      valid_to: { type: "date", required: false },
    },
  },
};

const DROP_ONLY = [
  "alliance_member",
  "candidacy_nomination",
  "government_party",
  "tenure_affiliation",
  "area_event_member",
  "party_relation",
  "party_affiliation",
];

export const GUIDE = {
  payload: {
    summary: "Required, up to 300 characters; it becomes the proposal's title.",
    rationale: "Optional, up to 10,000 characters: why the change is right.",
    polity: "Optional polity UUID the change belongs to.",
    ops: "1 to 500 ops, compiled in order into one changeset.",
    evidence:
      "A map from your own keys (letters, digits, _ and -, up to 40) to evidence items; ops cite them by key in their evidence array.",
  },
  ids: 'Every id is a UUID; find them with search, the get_ tools, and get_editable. An add can take a ref (a short name of your choosing), and later ops in the same proposal point at the new row as "$ref".',
  evidence_forms: {
    url: {
      shape: {
        url: "https://…",
        title: "required: the document's title",
        publisher: "required: who published it",
        kind: "required: official_body, aggregator, compendium, news_agency, academic, or community",
        quote: "the exact text or figure as printed",
        page: "page number, for a PDF",
      },
      note: "The archive fetches and stores the URL (and asks the Wayback Machine for a copy) before the checks run. A page that refuses automated fetches (403, a challenge, or a login) fails the check: upload the document instead, and never try to get around the block.",
    },
    upload: {
      shape: {
        upload: "an upload UUID from upload_document",
        title: "required: the document's title",
        publisher: "required: who published it",
        kind: "required: as for url",
        page: "page number, 1-based; required with a region",
        region:
          "[x0, y0, x1, y1] as fractions of the page from its top left, x0 < x1 and y0 < y1, the box around what you cite",
        quote: "the exact text or figure as printed",
      },
      note: "Wait until the upload's status is ready and read its pages with get_upload_page before marking a region.",
    },
    source: {
      shape: {
        source: "the UUID of a source the archive already holds",
        locator: "where in it, free-form",
        quote: "the exact text or figure as printed",
      },
    },
  },
  ops: {
    add: {
      shape: {
        op: "add",
        table: "name",
        ref: "n1",
        values: { "…": "the row's columns" },
        evidence: ["e1"],
      },
      note: "Inserts a row; required columns must be present.",
    },
    set: {
      shape: {
        op: "set",
        table: "name",
        id: "uuid of the row",
        values: { text: "…" },
        evidence: ["e1"],
      },
      note: "Changes the given columns of one row; a set that changes nothing is refused. party_color is keyed instead: key {party_id, valid_from} and values {color}.",
    },
    end: {
      shape: {
        op: "end",
        table: "party_color",
        key: { party_id: "uuid", valid_from: "YYYY-MM-DD or null" },
        values: { valid_to: "YYYY-MM-DD" },
        evidence: ["e1"],
      },
      note: "Closes a dated row.",
    },
    drop: {
      shape: {
        op: "drop",
        table: "note",
        id: "uuid",
        reason: "why the row is wrong",
        evidence: ["e1"],
      },
      note: "Removes a row by id, or by key for keyed tables (external_id takes key {scheme, value}). The reason defaults to the summary.",
    },
    cite: {
      shape: { op: "cite", entity_id: "uuid", evidence: ["e1"] },
      note: "Adds a citation of a source to a record that doesn't cite it yet.",
    },
    label: {
      shape: {
        op: "label",
        values: {
          entity_id: "uuid",
          kind: "ballot, integrity, recognition, or data_quality",
          value: "what the label says",
          assessor: "the outside body that made the assessment",
          valid_from: "YYYY-MM-DD, optional",
          valid_to: "YYYY-MM-DD, optional",
        },
        evidence: ["e1"],
      },
      note: "Adds a label to a record, citing the first evidence key as its source. ballot goes on a contest that lists no candidates, and its only value is no_candidate_qualified (it starts on the election date unless given). integrity goes on an election or polity and recognition on a polity or area; both name an outside assessor, such as OSCE/ODIHR, because the archive never makes those judgments itself. data_quality goes on any record. Remove a label with drop by id.",
    },
    translate: {
      shape: {
        op: "translate",
        of: { table: "note", id: "uuid" },
        lang: "BCP 47 tag",
        body: "the translated text",
        origin: "human, machine, or official",
      },
      note: "Adds or replaces one language's translation of a note. Only notes can be translated so far, and a machine translation never replaces a human or official one. Translate a note in a separate proposal from an edit to it.",
    },
    results: {
      shape: {
        op: "results",
        contest_id: "uuid",
        stage:
          "preliminary, official, certified, court_revised, recount, or alternative",
        as_of: "YYYY-MM-DD, optional: the date the document reports",
        rows: [
          {
            unit: "a reporting unit uuid, or { scheme, code } such as { scheme: 'us.county', code: '06037' }",
            option:
              "a contest_option uuid; omit for a unit-level measure such as registered or ballots_cast",
            measure:
              "votes, registered, ballots_cast, valid, invalid, blank, …",
            channel:
              "all (the default), election_day, early, postal, abroad, provisional, …",
            count_round: "0 (the default); 1, 2, … for ranked-choice rounds",
            value: 1234,
            page: "upload page, 1-based; defaults to the evidence's page",
            region:
              "[x0, y0, x1, y1] fractions of that page around the printed figure",
          },
        ],
        outcomes: [
          {
            option: "uuid",
            kind: "elected, won, advanced, eliminated, lost, tied, withdrew, disqualified, or declined",
            seats: "whole number, optional",
          },
        ],
        evidence: ["e1"],
      },
      note: "Adds a new result set for a contest from one document: up to 50,000 rows. The first evidence key is the set's source, and page and region need it to be an upload, so every figure links to its place on the page. Units and options come from get_editable (results) and get_result_cells. The set becomes the contest's default for its stage only when none is held and no accounting check fires (parts that don't add up to their totals); otherwise it is added alongside the existing one for review.",
    },
    correct: {
      shape: {
        op: "correct",
        result_set: "uuid of an active result set",
        cells: [
          {
            unit: "uuid or { scheme, code }",
            option: "uuid, or omitted",
            measure: "votes",
            channel: "all",
            count_round: 0,
            value: 1234,
          },
        ],
        evidence: ["e1"],
      },
      note: "Changes up to 2,000 figures in a published result set; each cell is addressed as in results, and a cell the set already holds at that value is refused. A set the archive's own pipelines wrote is never edited in place: once approved, the correction is folded into that pipeline's next run, and the proposal shows as awaiting fold until the rerun matches every cell. Read the current figures with get_result_cells first.",
    },
    image: {
      shape: {
        op: "image",
        table: "party_image",
        values: { party_id: "uuid", file: "File:Logo.svg" },
      },
      note: "Adds a party logo or a person's portrait from Wikimedia Commons; only public domain, CC0, CC BY, and CC BY-SA files are accepted.",
    },
  },
  tables: TABLES,
  drop_only: DROP_ONLY,
  not_yet:
    "Merges, retirements, new elections and contests, boundary uploads, and adds or changes on other tables are planned. Until they ship, describe the change in a note proposal's rationale or wait.",
  tiers:
    "Tier 1 changes can be approved by the editorial desk alone; everything else waits for an administrator. A proposal takes the highest tier of the rows it changes. New results are tier 1 only when they come from an official body's document with its URL and stored copy, declare no outcomes, raise no check findings, and no default set is held for that contest and stage; otherwise tier 2. Any correct op is tier 3.",
};

const DESCRIBED = new Map(Object.entries(TABLES));

const Table = v.picklist([...Object.keys(TABLES), ...DROP_ONLY]);

export const agents = {
  whoami: keyed
    .meta(
      openapi({
        method: "GET",
        path: "/whoami",
        summary:
          "The key's owner, roles, and key defaults, with write limits and today's proposals",
      }),
    )
    .handler(async ({ context: { owner } }) => {
      const [{ rows }, allowed] = await Promise.all([
        db.execute<{
          name: string;
          handle: string | null;
          key: string | null;
          expires_at: string | null;
          drafts: number;
          open: number;
          today: number;
          uploaded_today: number;
        }>(sql`
          select u.name, p.handle, k.name as key, k.expires_at::text,
                 (select count(*)::int from proposal where author_user_id = u.id and state = 'draft') as drafts,
                 (select count(*)::int from proposal where author_user_id = u.id
                    and state in ('open', 'changes_requested', 'approved')) as open,
                 (select count(*)::int from proposal where author_user_id = u.id
                    and created_at > now() - interval '1 day') as today,
                 (select coalesce(sum(bytes), 0)::float8 from upload where owner_user_id = u.id
                    and created_at > now() - interval '1 day') as uploaded_today
          from users u
          left join profile p on p.user_id = u.id
          left join apikeys k on k.id = ${owner.keyId}
          where u.id = ${owner.userId}
        `),
        roles(owner.userId),
      ]);
      const found = rows[0];
      if (found == null) {
        throw new ORPCError("NOT_FOUND");
      }
      return {
        owner: { name: found.name, handle: found.handle },
        roles: Object.entries(allowed)
          .filter(([, held]) => held)
          .map(([role]) => role),
        key: {
          name: found.key,
          expires_at: found.expires_at,
          agent_name: owner.agentName,
          agent_model: owner.agentModel,
        },
        limits: {
          writes_per_minute: WRITES.minute,
          writes_per_day: WRITES.day,
          upload_bytes_per_day: 2_000_000_000,
          uploaded_bytes_today: found.uploaded_today,
        },
        proposals: {
          drafts: found.drafts,
          open: found.open,
          created_today: found.today,
        },
      };
    }),
  ops: reader
    .meta(
      openapi({
        method: "GET",
        path: "/ops",
        summary:
          "The proposal payload, the evidence forms, the op vocabulary, and each editable table's columns, types, enums, and tier; pass table to see one",
      }),
    )
    .input(v.object({ table: v.optional(Table) }))
    .handler(({ input }) => {
      if (input.table == null) {
        return GUIDE;
      }
      return {
        table: input.table,
        ...(DESCRIBED.get(input.table) ?? {
          ops: ["drop"],
          note: "Rows can be dropped by id or key.",
        }),
        ops_reference: GUIDE.ops,
        evidence_forms: GUIDE.evidence_forms,
      };
    }),
  editable: reader
    .meta(
      openapi({
        method: "GET",
        path: "/entities/{id}/editable",
        summary:
          "One record's editable rows with their IDs: names, notes, external IDs, colors, images, sources, citations, and the open proposals that touch it",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .handler(async ({ input }) => run(async () => editable(input.id))),
  cells: reader
    .meta(
      openapi({
        method: "GET",
        path: "/result-sets/{id}/cells",
        summary:
          "One result set's figures with their unit, option, measure, channel, and round, and the page and region each was read from, paged in unit code order",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        unit: v.optional(v.pipe(v.string(), v.uuid())),
        offset: v.optional(
          v.pipe(
            v.union([v.number(), v.string()]),
            v.transform(Number),
            v.integer(),
            v.minValue(0),
          ),
          0,
        ),
        limit: v.optional(
          v.pipe(
            v.union([v.number(), v.string()]),
            v.transform(Number),
            v.integer(),
            v.minValue(1),
            v.maxValue(2000),
          ),
          500,
        ),
      }),
    )
    .handler(async ({ input }) => run(async () => cells(input))),
};
