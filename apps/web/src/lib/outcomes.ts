import { m } from "#lib/paraglide/messages.js";

export const OUTCOMES: Record<string, () => string> = {
  elected: m.outcome_elected,
  won: m.outcome_won,
  advanced: m.outcome_advanced,
  tied: m.outcome_tied,
  lost: m.outcome_lost,
  withdrew: m.outcome_withdrew,
  disqualified: m.outcome_disqualified,
};
export const LOST = new Set(["lost", "withdrew", "disqualified"]);
export const MEASURES: Record<string, () => string> = {
  list_votes: m.measure_list_votes,
  electoral_votes: m.measure_electoral_votes,
  state_delegate_equivalents: m.measure_state_delegate_equivalents,
};
