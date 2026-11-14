import { m } from "#lib/paraglide/messages.js";

export type FeedEntry = {
  id: string;
  at: string;
  title: string;
  polity: string | null;
  polity_path: string | null;
  pipeline: string;
  batches: number;
};

export type HistoryEntry = {
  changeset: string;
  at: string;
  author_kind: string;
  pipeline: string | null;
  summary: string;
  role: "created" | "changed" | "merged" | "retired" | "cited";
};

export const roleLabel: Record<HistoryEntry["role"], () => string> = {
  created: m.history_role_created,
  changed: m.history_role_changed,
  merged: m.history_role_merged,
  retired: m.history_role_retired,
  cited: m.history_role_cited,
};

export const importedBy = (pipeline: string | null) =>
  pipeline == null
    ? m.history_unattributed()
    : m.feed_imported_by({ pipeline });
