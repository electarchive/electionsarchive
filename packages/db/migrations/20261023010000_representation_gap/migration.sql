alter type ended_via add value 'withdrew';

create type representation_gap_reason as enum ('secession');

create table representation_gap (
  body_id uuid not null references body (id) on delete cascade,
  area_id uuid not null references area (id),
  reason representation_gap_reason not null,
  valid_from date not null,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (body_id, area_id, valid without overlaps)
);

select ea_version('representation_gap'::regclass);
