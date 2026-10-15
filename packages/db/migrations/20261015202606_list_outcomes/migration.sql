alter table outcome add column candidacy_id uuid references candidacy (id) on delete cascade;
alter table outcome drop constraint outcome_pkey;
alter table outcome add constraint outcome_key unique nulls not distinct (result_set_id, option_id, candidacy_id, kind);
create index outcome_candidacy_idx on outcome (candidacy_id) where candidacy_id is not null;

alter table outcome_history rename to outcome_history_before;
create table outcome_history (like outcome, recorded tstzrange not null, superseded_by uuid not null);
insert into outcome_history (result_set_id, option_id, kind, seats, notes, changeset_id, recorded_from, recorded, superseded_by)
select result_set_id, option_id, kind, seats, notes, changeset_id, recorded_from, recorded, superseded_by
from outcome_history_before;
drop table outcome_history_before;
