import { m } from "#lib/paraglide/messages.js";

export type ProposalState =
  | "draft"
  | "open"
  | "changes_requested"
  | "approved"
  | "published"
  | "rejected"
  | "withdrawn"
  | "reverted";

export type NameKind =
  | "official"
  | "native"
  | "english"
  | "historical"
  | "transliteration"
  | "abbreviation"
  | "ballot"
  | "short";

export type DiffRow = {
  table: string;
  key: Record<string, string>;
  entity_id: string | null;
  op: "add" | "change";
  fields: Record<string, [unknown, unknown]>;
};

export type Diff = {
  rows: Array<DiffRow>;
  truncated: boolean;
  entities: Array<string>;
};

export type Checks = {
  diff?: Record<string, Record<string, number>>;
  findings?: Record<string, number>;
  examples?: Record<string, Array<{ details?: { pipeline?: string } }>>;
  unchanged?: boolean;
};

export type Editable = {
  names: Array<{
    id: string;
    text: string;
    kind: NameKind;
    lang: string | null;
    script: string | null;
    is_preferred: boolean;
    shown: boolean;
    source_id: string | null;
    source: string | null;
    changed: string | null;
  }>;
  sources: Array<{ id: string; title: string; publisher: string | null }>;
  open: Array<{ number: number; title: string; state: ProposalState }>;
};

export const stateLabel: Record<ProposalState, () => string> = {
  draft: m.proposal_state_draft,
  open: m.proposal_state_open,
  changes_requested: m.proposal_state_changes_requested,
  approved: m.proposal_state_approved,
  published: m.proposal_state_published,
  rejected: m.proposal_state_rejected,
  withdrawn: m.proposal_state_withdrawn,
  reverted: m.proposal_state_reverted,
};

export const tierLabel: Record<number, () => string> = {
  1: m.proposal_tier_1,
  2: m.proposal_tier_2,
  3: m.proposal_tier_3,
};

export const sourceKinds = [
  "official_body",
  "aggregator",
  "compendium",
  "news_agency",
  "academic",
  "community",
] as const;

export const sourceKindLabel: Record<
  (typeof sourceKinds)[number],
  () => string
> = {
  official_body: m.source_kind_official_body,
  aggregator: m.source_kind_aggregator,
  compendium: m.source_kind_compendium,
  news_agency: m.source_kind_news_agency,
  academic: m.source_kind_academic,
  community: m.source_kind_community,
};

export const kindLabel: Record<NameKind, () => string> = {
  official: m.name_kind_official,
  native: m.name_kind_native,
  english: m.name_kind_english,
  historical: m.name_kind_historical,
  transliteration: m.name_kind_transliteration,
  abbreviation: m.name_kind_abbreviation,
  ballot: m.name_kind_ballot,
  short: m.name_kind_short,
};
