alter type entity_type add value 'government';

create or replace function ea_prefix(t entity_type) returns text language sql immutable strict parallel safe as $$
  select case t::text
    when 'polity' then 'pl' when 'area' then 'ar' when 'reporting_unit' then 'ru'
    when 'election' then 'el' when 'contest' then 'ct' when 'contest_option' then 'op'
    when 'result_set' then 'rs' when 'electoral_system' then 'es' when 'party' then 'pt'
    when 'alliance' then 'al' when 'person' then 'pe' when 'candidacy' then 'cd'
    when 'body' then 'bd' when 'office' then 'of' when 'seat' then 'st' when 'tenure' then 'te'
    when 'source' then 'sr' when 'saved_view' then 'vw' when 'government' then 'gv'
  end
$$;

create type government_party_role as enum ('head', 'member', 'support');

create table government (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  polity_id uuid not null references polity (id),
  office_id uuid references office (id),
  head_person_id uuid references person (id),
  from_date date not null,
  from_precision date_precision not null default 'day',
  to_date date,
  to_precision date_precision not null default 'day',
  election_id uuid references election (id),
  previous_government_id uuid references government (id),
  caretaker boolean not null default false,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (to_date is null or to_date >= from_date)
);
create index government_polity_idx on government (polity_id, from_date);
create index government_election_idx on government (election_id);

create table government_party (
  government_id uuid not null references government (id) on delete cascade,
  party_id uuid not null references party (id),
  role government_party_role not null,
  seats integer check (seats >= 0),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (government_id, party_id)
);
create index government_party_party_idx on government_party (party_id);

create trigger ea_entity before insert on government for each row execute function ea_entity('government');
select ea_version('government'::regclass);
select ea_version('government_party'::regclass);
