set lock_timeout = '5s';
--> statement-breakpoint
alter table candidacy
  add column preference_option_id uuid references contest_option (id),
  add constraint candidacy_preference_option_key unique (preference_option_id) deferrable initially deferred,
  add constraint candidacy_preference_option_check check (preference_option_id is distinct from option_id);
--> statement-breakpoint
comment on column candidacy.preference_option_id is
  'The candidate option, under this candidacy''s list option, that carries its preference votes (measure 7); the list''s own votes stay on the list option';
--> statement-breakpoint
alter table candidacy_history add column preference_option_id uuid;
--> statement-breakpoint
create or replace function candidacy_capture() returns trigger language plpgsql as $$
begin
  insert into candidacy_history (
    id, person_id, option_id, role, list_position, ballot_name, member_of_party_id, is_independent, is_incumbent,
    ballot_lines, changeset_id, recorded_from, recorded, superseded_by, preference_option_id
  )
  select
    o.id, o.person_id, o.option_id, o.role, o.list_position, o.ballot_name, o.member_of_party_id, o.is_independent,
    o.is_incumbent, o.ballot_lines, o.changeset_id, o.recorded_from, tstzrange(o.recorded_from, now()),
    ea_current_changeset(), o.preference_option_id
  from old_rows o;
  return null;
end $$;
