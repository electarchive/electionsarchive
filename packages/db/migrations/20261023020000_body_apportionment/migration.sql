alter table seat add column apportioned_area_id uuid references area (id);

alter table seat_history rename to seat_history_before;
create table seat_history (like seat, recorded tstzrange not null, superseded_by uuid not null);
insert into seat_history (id, body_id, district_area_id, label, valid_from, valid_from_precision, valid_to,
                          valid_to_precision, valid, changeset_id, recorded_from, voting, recorded, superseded_by)
select id, body_id, district_area_id, label, valid_from, valid_from_precision, valid_to, valid_to_precision, valid,
       changeset_id, recorded_from, voting, recorded, superseded_by
from seat_history_before;
drop table seat_history_before;

create table body_apportionment (
  body_id uuid not null references body (id) on delete cascade,
  area_id uuid not null references area (id),
  seats int not null,
  valid_from date not null,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (body_id, area_id, valid without overlaps)
);

select ea_version('body_apportionment'::regclass);
