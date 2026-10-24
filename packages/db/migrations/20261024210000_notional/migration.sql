alter type stage add value 'notional';
--> statement-breakpoint
alter table contest add column notional boolean not null default false;
--> statement-breakpoint
alter table contest_history rename to contest_history_before;
--> statement-breakpoint
create table contest_history (like contest, recorded tstzrange not null, superseded_by uuid not null);
--> statement-breakpoint
insert into contest_history (id, election_id, kind, office_id, body_id, district_area_id, seat_id, boundary_set_id,
                             magnitude, party_scope_id, round_number, previous_round_id, allocation_parent_id,
                             electoral_system_id, rules_override, default_result_kind, changeset_id, recorded_from, term,
                             notional, recorded, superseded_by)
select id, election_id, kind, office_id, body_id, district_area_id, seat_id, boundary_set_id, magnitude,
       party_scope_id, round_number, previous_round_id, allocation_parent_id, electoral_system_id, rules_override,
       default_result_kind, changeset_id, recorded_from, term, false, recorded, superseded_by
from contest_history_before;
--> statement-breakpoint
drop table contest_history_before;
