import { m } from "#lib/paraglide/messages.js";

export const OUTCOMES: Record<string, () => string> = {
  elected: m.outcome_elected,
  won: m.outcome_won,
  advanced: m.outcome_advanced,
  tied: m.outcome_tied,
  lost: m.outcome_lost,
  withdrew: m.outcome_withdrew,
  disqualified: m.outcome_disqualified,
  declined: m.outcome_declined,
};
export const LOST = new Set(["lost", "withdrew", "disqualified", "declined"]);
export const VACANCIES: Record<string, (input: { name: string }) => string> = {
  died: m.byes_reason_died,
  resigned: m.byes_reason_resigned,
  elevated: m.byes_reason_elevated,
  appointed: m.byes_reason_appointed,
  expelled: m.byes_reason_expelled,
  disqualified: m.byes_reason_disqualified,
  annulled: m.byes_reason_annulled,
  removed: m.byes_reason_removed,
  withdrew: m.byes_reason_withdrew,
};
export const MEASURES: Record<string, () => string> = {
  list_votes: m.measure_list_votes,
  electoral_votes: m.measure_electoral_votes,
  state_delegate_equivalents: m.measure_state_delegate_equivalents,
};
export const isDerived = (note: string) => note.startsWith("Derived: ");
export const explain = (note: string) =>
  isDerived(note) ? m.outcome_derived({ rest: note.slice(9) }) : note;
