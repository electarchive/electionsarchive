create table party_position (
  party_id uuid not null references party (id) on delete cascade,
  low smallint not null check (low between -3 and 3),
  high smallint not null check (high between low and 3),
  rank smallint,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (party_id, valid without overlaps)
);

select ea_version('party_position'::regclass);
