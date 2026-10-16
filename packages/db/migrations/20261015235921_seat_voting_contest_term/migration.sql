alter table seat add column voting boolean not null default true;

alter table seat_history rename to seat_history_before;
create table seat_history (like seat, recorded tstzrange not null, superseded_by uuid not null);
insert into seat_history (id, body_id, district_area_id, label, valid_from, valid_from_precision, valid_to,
                          valid_to_precision, valid, changeset_id, recorded_from, recorded, superseded_by)
select id, body_id, district_area_id, label, valid_from, valid_from_precision, valid_to, valid_to_precision, valid,
       changeset_id, recorded_from, recorded, superseded_by
from seat_history_before;
drop table seat_history_before;

create type contest_term as enum ('full', 'remainder');
alter table contest add column term contest_term;

alter table contest_history rename to contest_history_before;
create table contest_history (like contest, recorded tstzrange not null, superseded_by uuid not null);
insert into contest_history (id, election_id, kind, office_id, body_id, district_area_id, seat_id, boundary_set_id,
                             magnitude, party_scope_id, round_number, previous_round_id, allocation_parent_id,
                             electoral_system_id, rules_override, default_result_kind, changeset_id, recorded_from,
                             recorded, superseded_by)
select id, election_id, kind, office_id, body_id, district_area_id, seat_id, boundary_set_id, magnitude,
       party_scope_id, round_number, previous_round_id, allocation_parent_id, electoral_system_id, rules_override,
       default_result_kind, changeset_id, recorded_from, recorded, superseded_by
from contest_history_before;
drop table contest_history_before;
