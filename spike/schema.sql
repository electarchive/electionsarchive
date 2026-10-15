create extension if not exists postgis;
create extension if not exists btree_gist;
create extension if not exists pg_trgm;
create extension if not exists unaccent;

create type date_precision as enum ('day', 'month', 'year', 'unknown');
create type calendar as enum ('gregorian', 'julian', 'rumi', 'hijri', 'french_republican');

create type entity_type as enum (
  'polity', 'area', 'reporting_unit', 'election', 'contest', 'contest_option', 'result_set',
  'electoral_system', 'party', 'alliance', 'person', 'candidacy', 'body', 'office', 'seat',
  'tenure', 'source', 'saved_view'
);

create function ea_base32(id uuid) returns text language plpgsql immutable strict parallel safe as $$
declare
  alphabet constant text := '0123456789abcdefghjkmnpqrstvwxyz';
  bits bit(130) := b'00' || ('x' || encode(uuid_send(id), 'hex'))::bit(128);
  out text := '';
begin
  for i in 0..25 loop
    out := out || substr(alphabet, substring(bits from i * 5 + 1 for 5)::int + 1, 1);
  end loop;
  return out;
end $$;

create function ea_base32_decode(code text) returns uuid language plpgsql immutable strict parallel safe as $$
declare
  alphabet constant text := '0123456789abcdefghjkmnpqrstvwxyz';
  bits text := '';
  hex text := '';
  digit int;
begin
  if length(code) <> 26 then
    raise exception 'invalid public id body: %', code;
  end if;
  for i in 1..26 loop
    digit := strpos(alphabet, substr(lower(code), i, 1)) - 1;
    if digit < 0 then
      raise exception 'invalid public id character in %', code;
    end if;
    bits := bits || digit::bit(5)::text;
  end loop;
  if left(bits, 2) <> '00' then
    raise exception 'invalid public id body: %', code;
  end if;
  for i in 0..31 loop
    hex := hex || to_hex(substring(bits from 3 + i * 4 for 4)::bit(4)::int);
  end loop;
  return hex::uuid;
end $$;

create function ea_resolve(public_id text) returns uuid language sql immutable strict parallel safe as $$
  select ea_base32_decode(split_part(public_id, '_', 2))
$$;

create function ea_prefix(t entity_type) returns text language sql immutable strict parallel safe as $$
  select case t
    when 'polity' then 'pl' when 'area' then 'ar' when 'reporting_unit' then 'ru'
    when 'election' then 'el' when 'contest' then 'ct' when 'contest_option' then 'op'
    when 'result_set' then 'rs' when 'electoral_system' then 'es' when 'party' then 'pt'
    when 'alliance' then 'al' when 'person' then 'pe' when 'candidacy' then 'cd'
    when 'body' then 'bd' when 'office' then 'of' when 'seat' then 'st' when 'tenure' then 'te'
    when 'source' then 'sr' when 'saved_view' then 'vw'
  end
$$;

create function ea_public_id(t entity_type, id uuid) returns text language sql immutable strict parallel safe as $$
  select ea_prefix(t) || '_' || ea_base32(id)
$$;

create type author_kind as enum ('pipeline', 'user', 'agent', 'system');
create type changeset_kind as enum ('import', 'edit', 'correction', 'match', 'digitization', 'live');
create type changeset_status as enum ('draft', 'pending_review', 'published', 'rejected', 'superseded');
create type payload_kind as enum ('ops', 'artifacts');

create table changeset (
  id uuid primary key default uuidv7(),
  author_kind author_kind not null,
  author_user_id uuid,
  pipeline text,
  pipeline_version text,
  kind changeset_kind not null,
  status changeset_status not null default 'draft',
  summary text not null,
  rationale text,
  scope jsonb not null default '{}',
  payload_kind payload_kind,
  payload jsonb,
  artifact_keys text[],
  checks jsonb,
  created_at timestamptz not null default now(),
  published_at timestamptz,
  reviewed_by uuid,
  reviewed_at timestamptz
);

create function ea_current_changeset() returns uuid language plpgsql stable as $$
declare
  v text := current_setting('ea.changeset_id', true);
begin
  if v is null or v = '' then
    raise exception 'writes require a changeset: set ea.changeset_id';
  end if;
  return v::uuid;
end $$;

create function ea_stamp() returns trigger language plpgsql as $$
begin
  new.changeset_id := ea_current_changeset();
  new.recorded_from := now();
  return new;
end $$;

create function ea_version(tbl regclass) returns void language plpgsql as $v$
declare
  capture text := tbl::text || '_capture';
begin
  execute format(
    'create table %s_history (like %s, recorded tstzrange not null, superseded_by uuid not null)',
    tbl, tbl
  );
  execute format(
    $f$create function %I() returns trigger language plpgsql as $b$
       begin
         insert into %s_history select o.*, tstzrange(o.recorded_from, now()), ea_current_changeset() from old_rows o;
         return null;
       end $b$ $f$,
    capture, tbl
  );
  execute format('create trigger ea_stamp before insert or update on %s for each row execute function ea_stamp()', tbl);
  execute format(
    'create trigger ea_history_update after update on %s referencing old table as old_rows'
    ' for each statement execute function %I()',
    tbl, capture
  );
  execute format(
    'create trigger ea_history_delete after delete on %s referencing old table as old_rows'
    ' for each statement execute function %I()',
    tbl, capture
  );
end $v$;

create table entity (
  id uuid primary key,
  type entity_type not null,
  changeset_id uuid not null references changeset (id),
  created_at timestamptz not null default now()
);

create function ea_entity() returns trigger language plpgsql as $$
begin
  insert into entity (id, type, changeset_id)
  values (new.id, tg_argv[0]::entity_type, ea_current_changeset())
  on conflict (id) do nothing;
  return new;
end $$;

create function ea_subtype(tbl regclass, t entity_type) returns void language plpgsql as $$
begin
  execute format(
    'create trigger ea_entity before insert on %s for each row execute function ea_entity(%L)',
    tbl, t
  );
end $$;

create type source_kind as enum (
  'official_body', 'aggregator', 'compendium', 'news_agency', 'academic', 'community', 'wikidata'
);

create table source (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  kind source_kind not null,
  publisher text not null,
  title text not null,
  url text,
  license text,
  license_url text,
  attribution text,
  notes text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table source_artifact (
  id uuid primary key default uuidv7(),
  source_id uuid not null references source (id),
  url text,
  sha256 text not null,
  retrieved_at timestamptz not null,
  storage_key text not null,
  media_type text,
  bytes bigint,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table source_page (
  id uuid primary key default uuidv7(),
  artifact_id uuid not null references source_artifact (id),
  page_number int not null,
  image_key text not null,
  width int not null,
  height int not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  unique (artifact_id, page_number)
);

create table citation (
  id uuid primary key default uuidv7(),
  entity_id uuid not null references entity (id) on delete cascade,
  source_id uuid not null references source (id),
  locator jsonb,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create type polity_kind as enum (
  'sovereign_state', 'supranational', 'federal_unit', 'region', 'county', 'municipality',
  'submunicipal', 'historical_state'
);

create table polity (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  kind polity_kind not null,
  iso_code text unique,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table polity_parent (
  polity_id uuid not null references polity (id),
  parent_id uuid not null references polity (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (polity_id, valid without overlaps)
);

create type polity_relation_kind as enum (
  'succeeded_by', 'merged_into', 'split_into', 'annexed_by', 'reunified_into', 'seceded_from'
);

create table polity_relation (
  id uuid primary key default uuidv7(),
  from_polity_id uuid not null references polity (id),
  to_polity_id uuid not null references polity (id),
  kind polity_relation_kind not null,
  date date,
  date_precision date_precision not null default 'day',
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create type polity_period_kind as enum ('constitutional_order', 'military_rule', 'occupation', 'transitional');

create table polity_period (
  id uuid primary key default uuidv7(),
  polity_id uuid not null references polity (id),
  kind polity_period_kind not null,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  notes text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create type scheme_kind as enum ('administrative', 'electoral_district', 'precinct', 'census', 'synthetic');

create table scheme (
  code text primary key check (code ~ '^[a-z]{2,3}(-[a-z0-9]+)?\.[a-z0-9_]+$'),
  polity_id uuid not null references polity (id),
  kind scheme_kind not null,
  rank int2 not null,
  noun text not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table area (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  k int4 generated always as identity unique,
  scheme_code text not null references scheme (code),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table polity_territory (
  polity_id uuid not null references polity (id),
  area_id uuid not null references area (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (polity_id, valid without overlaps)
);

create table area_code (
  area_id uuid not null references area (id) on delete cascade,
  code text not null,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (area_id, valid without overlaps)
);
create index area_code_code_idx on area_code (code);

create table area_parent (
  area_id uuid not null references area (id) on delete cascade,
  parent_id uuid not null references area (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (area_id, valid without overlaps)
);
create index area_parent_parent_idx on area_parent (parent_id);

create type geometry_quality as enum ('official', 'osm', 'academic', 'digitized', 'synthetic');

create table area_geometry (
  area_id uuid not null references area (id) on delete cascade,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  geom geometry(MultiPolygon, 4326) not null,
  label_point geometry(Point, 4326),
  quality geometry_quality not null,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (area_id, valid without overlaps)
);
create index area_geometry_geom_idx on area_geometry using gist (geom);

create table boundary_set (
  id uuid primary key default uuidv7(),
  scheme_code text not null references scheme (code),
  vintage text not null,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  as_of date not null,
  tiles_key text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  unique (scheme_code, vintage)
);

create type area_event_kind as enum (
  'created', 'dissolved', 'renamed', 'recoded', 'split', 'merged', 'absorbed', 'boundary_changed',
  'transferred'
);

create table area_event (
  id uuid primary key default uuidv7(),
  kind area_event_kind not null,
  date date,
  date_precision date_precision not null default 'day',
  source_id uuid references source (id),
  notes text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create type area_event_role as enum ('from', 'to');

create table area_event_member (
  event_id uuid not null references area_event (id) on delete cascade,
  area_id uuid not null references area (id),
  role area_event_role not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (event_id, area_id, role)
);

create type area_relation as enum ('equals', 'within', 'unlocated');
create type reporting_unit_kind as enum (
  'standard', 'abroad_mission', 'customs_gate', 'prison', 'mobile', 'postal_district', 'aggregate'
);

create table reporting_unit (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  k int4 generated always as identity unique,
  polity_id uuid not null references polity (id),
  scheme_code text not null references scheme (code),
  code text not null,
  parent_id uuid references reporting_unit (id),
  counted_with_id uuid references reporting_unit (id),
  area_relation area_relation not null,
  area_id uuid references area (id),
  location geometry(Point, 4326),
  kind reporting_unit_kind not null default 'standard',
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  unique (scheme_code, code, valid without overlaps),
  check ((area_relation = 'unlocated') = (area_id is null))
);
create index reporting_unit_parent_idx on reporting_unit (parent_id);
create index reporting_unit_area_idx on reporting_unit (area_id);

create table reporting_unit_area (
  unit_id uuid not null references reporting_unit (id) on delete cascade,
  area_id uuid not null references area (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (unit_id, area_id, valid without overlaps)
);
create index reporting_unit_area_area_idx on reporting_unit_area (area_id);

create type crosswalk_from_kind as enum ('boundary_set', 'reporting_scheme');
create type crosswalk_method as enum ('exact', 'registered_voters', 'population', 'area');

create table crosswalk (
  id uuid primary key default uuidv7(),
  from_kind crosswalk_from_kind not null,
  from_boundary_set_id uuid references boundary_set (id),
  from_scheme_code text references scheme (code),
  from_as_of date,
  to_boundary_set_id uuid not null references boundary_set (id),
  method crosswalk_method not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table crosswalk_weight (
  crosswalk_id uuid not null references crosswalk (id) on delete cascade,
  from_k int4 not null,
  to_k int4 not null,
  weight numeric not null,
  exact boolean not null,
  primary key (crosswalk_id, from_k, to_k)
);

create type party_kind as enum (
  'party', 'independent_group', 'list_vehicle', 'federation', 'bloc', 'nuance', 'parliamentary_group'
);

create table party (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  kind party_kind not null default 'party',
  polity_id uuid references polity (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table party_color (
  party_id uuid not null references party (id) on delete cascade,
  color text not null check (color ~ '^#[0-9a-f]{6}$'),
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

create type party_relation_kind as enum (
  'merged_into', 'split_from', 'successor_of', 'refounded_as', 'absorbed_by', 'closed_by_court',
  'list_vehicle_for'
);

create type party_affiliation_kind as enum ('ep_group', 'federation', 'international', 'regional_branch');

create table party_affiliation (
  party_id uuid not null references party (id) on delete cascade,
  parent_party_id uuid not null references party (id),
  kind party_affiliation_kind not null,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (party_id, kind, valid without overlaps)
);

create table person (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  birth_date date,
  birth_precision date_precision,
  death_date date,
  death_precision date_precision,
  gender text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table person_party_membership (
  person_id uuid not null references person (id) on delete cascade,
  party_id uuid not null references party (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (person_id, valid without overlaps)
);

create type body_kind as enum (
  'lower_house', 'upper_house', 'unicameral', 'council', 'electoral_college', 'constituent_assembly',
  'appointed_assembly'
);

create table body (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  polity_id uuid not null references polity (id),
  kind body_kind not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table body_size (
  body_id uuid not null references body (id) on delete cascade,
  seats int not null,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (body_id, valid without overlaps)
);

create type body_event_kind as enum ('dissolved', 'suspended', 'reconstituted', 'replaced_by_appointed');

create table body_event (
  id uuid primary key default uuidv7(),
  body_id uuid not null references body (id),
  kind body_event_kind not null,
  date date,
  date_precision date_precision not null default 'day',
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create type office_kind as enum (
  'head_of_state', 'head_of_government', 'governor', 'mayor', 'deputy_mayor', 'muhtar', 'speaker',
  'other'
);

create table office (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  polity_id uuid not null references polity (id),
  body_id uuid references body (id),
  kind office_kind not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table seat (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  body_id uuid not null references body (id),
  district_area_id uuid references area (id),
  label text,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table electoral_system (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  polity_id uuid not null references polity (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  rules jsonb not null,
  rules_version int not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create type election_kind as enum (
  'general', 'by_election', 'primary', 'caucus', 'runoff', 'referendum', 'recall', 'indirect', 'local',
  'special'
);
create type election_status as enum ('scheduled', 'held', 'postponed', 'canceled', 'annulled');

create table election (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  polity_id uuid not null references polity (id),
  parent_id uuid references election (id),
  kind election_kind not null,
  date_from date,
  date_to date,
  date_precision date_precision not null default 'day',
  original_calendar calendar,
  original_text text,
  status election_status not null,
  rerun_of_id uuid references election (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (date_to >= date_from)
);
create index election_polity_idx on election (polity_id, date_from);
create index election_parent_idx on election (parent_id);

create type alliance_kind as enum ('electoral_alliance', 'joint_list', 'federation', 'apparentement');

create table alliance (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  kind alliance_kind not null,
  election_id uuid references election (id),
  parent_alliance_id uuid references alliance (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table alliance_member (
  alliance_id uuid not null references alliance (id) on delete cascade,
  party_id uuid not null references party (id),
  role text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (alliance_id, party_id)
);

create table party_relation (
  id uuid primary key default uuidv7(),
  from_party_id uuid not null references party (id),
  to_party_id uuid references party (id),
  kind party_relation_kind not null,
  date date,
  date_precision date_precision not null default 'day',
  election_id uuid references election (id),
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check ((kind = 'closed_by_court') = (to_party_id is null))
);

create type contest_kind as enum (
  'office', 'body_seats', 'list_allocation', 'ballot_question', 'elector_slate', 'delegate_allocation'
);
create type result_kind as enum ('counted', 'tacit', 'show_of_hands', 'appointed');

create table contest (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  election_id uuid not null references election (id),
  kind contest_kind not null,
  office_id uuid references office (id),
  body_id uuid references body (id),
  district_area_id uuid references area (id),
  seat_id uuid references seat (id),
  boundary_set_id uuid references boundary_set (id),
  magnitude int,
  party_scope_id uuid references party (id),
  round_number int2 not null default 1,
  previous_round_id uuid references contest (id),
  allocation_parent_id uuid references contest (id),
  electoral_system_id uuid references electoral_system (id),
  rules_override jsonb,
  default_result_kind result_kind not null default 'counted',
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);
create index contest_election_idx on contest (election_id);
create index contest_office_idx on contest (office_id);

create type ballot_question_type as enum (
  'initiative', 'mandatory_referendum', 'optional_referendum', 'counter_proposal', 'tie_break',
  'plebiscite', 'recall_question'
);

create table ballot_question (
  contest_id uuid primary key references contest (id) on delete cascade,
  type ballot_question_type not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create type option_kind as enum (
  'candidate', 'ticket', 'list', 'legend', 'answer', 'write_in', 'others', 'none_of_the_above', 'uncommitted'
);

create table contest_option (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  k int4 generated always as identity (start with 1) unique,
  contest_id uuid not null references contest (id),
  kind option_kind not null,
  parent_option_id uuid references contest_option (id),
  party_id uuid references party (id),
  alliance_id uuid references alliance (id),
  ballot_position int,
  ballot_label text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);
create index contest_option_contest_idx on contest_option (contest_id);

create type candidacy_role as enum ('head', 'running_mate', 'list_member', 'substitute');

create table candidacy (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  person_id uuid not null references person (id),
  option_id uuid not null references contest_option (id),
  role candidacy_role not null default 'head',
  list_position int,
  ballot_name text,
  member_of_party_id uuid references party (id),
  is_independent boolean not null default false,
  is_incumbent boolean,
  ballot_lines int2 not null default 1 check (ballot_lines between 1 and 3),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);
create index candidacy_person_idx on candidacy (person_id);
create index candidacy_option_idx on candidacy (option_id);

create type nomination_kind as enum ('nominated', 'endorsed', 'supported');

create table candidacy_nomination (
  candidacy_id uuid not null references candidacy (id) on delete cascade,
  party_id uuid not null references party (id),
  kind nomination_kind not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (candidacy_id, party_id)
);

create type stage as enum (
  'live', 'preliminary', 'official', 'certified', 'court_revised', 'recount', 'alternative'
);
create type result_set_status as enum ('active', 'superseded', 'annulled', 'withdrawn');

create table result_set (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  k int4 generated always as identity unique,
  election_id uuid not null references election (id),
  contest_id uuid references contest (id),
  stage stage not null,
  kind result_kind not null default 'counted',
  source_id uuid not null references source (id),
  as_of timestamptz,
  status result_set_status not null default 'active',
  is_default boolean not null default false,
  supersedes_id uuid references result_set (id),
  completeness numeric,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);
create unique index result_set_default_idx on result_set (election_id, contest_id, stage) nulls not distinct where is_default;
create index result_set_contest_idx on result_set (contest_id);

create table measure (
  id int2 primary key,
  code text not null unique,
  description text not null
);

create table channel (
  id int2 primary key,
  code text not null unique,
  description text not null
);

create table result_value (
  result_set_k int4 not null,
  unit_k int4 not null,
  option_k int4 not null default 0,
  channel int2 not null default 0,
  measure int2 not null,
  count_round int2 not null default 0,
  flags int2 not null default 0,
  value numeric not null,
  primary key (result_set_k, unit_k, option_k, channel, measure, count_round)
) partition by hash (result_set_k);

do $$
begin
  for i in 0..31 loop
    execute format(
      'create table result_value_p%s partition of result_value for values with (modulus 32, remainder %s)',
      i, i
    );
  end loop;
end $$;

create table value_locator (
  result_set_k int4 not null,
  unit_k int4 not null,
  option_k int4 not null default 0,
  channel int2 not null default 0,
  measure int2 not null,
  count_round int2 not null default 0,
  page_id uuid references source_page (id),
  source_id uuid references source (id),
  x0 real,
  y0 real,
  x1 real,
  y1 real,
  primary key (result_set_k, unit_k, option_k, channel, measure, count_round),
  check (num_nonnulls(page_id, source_id) >= 1),
  check ((page_id is null) = (x0 is null))
);

create table result_flow (
  result_set_k int4 not null,
  unit_k int4 not null,
  from_option_k int4 not null,
  option_k int4 not null,
  channel int2 not null default 0,
  measure int2 not null,
  count_round int2 not null default 0,
  flags int2 not null default 0,
  value numeric not null,
  primary key (result_set_k, unit_k, from_option_k, option_k, channel, measure, count_round)
) partition by hash (result_set_k);

do $$
begin
  for i in 0..31 loop
    execute format(
      'create table result_flow_p%s partition of result_flow for values with (modulus 32, remainder %s)',
      i, i
    );
  end loop;
end $$;

create table result_rollup (
  result_set_k int4 not null,
  area_k int4 not null,
  option_k int4 not null default 0,
  channel int2 not null default 0,
  measure int2 not null,
  count_round int2 not null default 0,
  exact boolean not null,
  value numeric not null,
  primary key (result_set_k, area_k, option_k, channel, measure, count_round)
);

create type outcome_kind as enum (
  'elected', 'won', 'advanced', 'eliminated', 'lost', 'tied', 'withdrew', 'disqualified'
);

create table outcome (
  result_set_id uuid not null references result_set (id) on delete cascade,
  option_id uuid not null references contest_option (id),
  kind outcome_kind not null,
  seats int,
  notes text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (result_set_id, option_id, kind)
);

create type acquired_via as enum (
  'elected', 'appointed', 'trustee', 'succession', 'list_replacement', 'co_opted', 'acting'
);
create type ended_via as enum (
  'term_end', 'resigned', 'died', 'removed', 'annulled', 'dissolved', 'expelled', 'lost_seat', 'disqualified'
);

create table tenure (
  id uuid primary key default uuidv7() references entity (id) on delete cascade,
  person_id uuid not null references person (id),
  office_id uuid references office (id),
  seat_id uuid references seat (id),
  body_id uuid references body (id),
  from_date date,
  from_precision date_precision not null default 'day',
  to_date date,
  to_precision date_precision not null default 'day',
  acquired_via acquired_via not null,
  contest_id uuid references contest (id),
  ended_via ended_via,
  resumes_tenure_id uuid references tenure (id),
  succeeds_tenure_id uuid references tenure (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (num_nonnulls(office_id, body_id) >= 1)
);
create index tenure_person_idx on tenure (person_id);
create index tenure_office_idx on tenure (office_id);

create type tenure_event_kind as enum (
  'elected', 'certified', 'certificate_denied', 'certificate_reversed', 'sworn_in', 'suspended',
  'removed', 'trustee_appointed', 'reinstated', 'resigned', 'died', 'immunity_lifted', 'arrested',
  'switched_party', 'became_independent', 'expelled', 'term_ended'
);

create table tenure_event (
  id uuid primary key default uuidv7(),
  tenure_id uuid not null references tenure (id) on delete cascade,
  kind tenure_event_kind not null,
  date date,
  date_precision date_precision not null default 'day',
  actor text,
  reason text,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create type tenure_affiliation_kind as enum ('party', 'parliamentary_group');

create table tenure_affiliation (
  tenure_id uuid not null references tenure (id) on delete cascade,
  kind tenure_affiliation_kind not null default 'party',
  party_id uuid references party (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (tenure_id, kind, valid without overlaps)
);

create type name_kind as enum (
  'official', 'native', 'english', 'historical', 'transliteration', 'abbreviation', 'ballot', 'short'
);

create table name (
  id uuid primary key default uuidv7(),
  entity_id uuid not null references entity (id) on delete cascade,
  text text not null,
  lang text not null,
  script text,
  kind name_kind not null,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  is_preferred boolean not null default false,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);
create index name_entity_idx on name (entity_id);

create table external_id (
  entity_id uuid not null references entity (id) on delete cascade,
  scheme text not null,
  value text not null,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (scheme, value)
);
create index external_id_entity_idx on external_id (entity_id);

create table slug (
  path text primary key,
  entity_id uuid not null references entity (id) on delete cascade,
  is_current boolean not null default true,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);
create unique index slug_current_idx on slug (entity_id) where is_current;

create type note_kind as enum ('context', 'caveat', 'methodology', 'editorial');

create table note (
  id uuid primary key default uuidv7(),
  entity_id uuid not null references entity (id) on delete cascade,
  kind note_kind not null,
  body_md text not null,
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);
create index note_entity_idx on note (entity_id);

create type label_kind as enum ('recognition', 'integrity', 'data_quality');

create table label (
  id uuid primary key default uuidv7(),
  entity_id uuid not null references entity (id) on delete cascade,
  kind label_kind not null,
  value text not null,
  assessor text,
  source_id uuid references source (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create type flag_severity as enum ('info', 'warning', 'error');
create type flag_status as enum ('open', 'accepted', 'resolved');

create table flag (
  id uuid primary key default uuidv7(),
  entity_id uuid references entity (id) on delete cascade,
  result_set_id uuid references result_set (id) on delete cascade,
  check_code text not null,
  severity flag_severity not null,
  details jsonb,
  status flag_status not null default 'open',
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check (num_nonnulls(entity_id, result_set_id) >= 1)
);

create table indicator (
  id uuid primary key default uuidv7(),
  code text not null unique,
  source_id uuid references source (id),
  vintage text not null,
  unit text,
  description text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table indicator_value (
  indicator_id uuid not null references indicator (id) on delete cascade,
  area_k int4 not null,
  value numeric not null,
  primary key (indicator_id, area_k)
);

create table finance_summary (
  id uuid primary key default uuidv7(),
  subject_entity_id uuid not null references entity (id) on delete cascade,
  election_id uuid references election (id),
  measure text not null,
  value numeric not null,
  currency char(3) not null,
  period daterange,
  source_id uuid references source (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now()
);

create table search_document (
  entity_id uuid primary key references entity (id) on delete cascade,
  type entity_type not null,
  polity_id uuid references polity (id),
  text text not null,
  tsv tsvector not null,
  rank_boost real not null default 0
);
create index search_document_trgm_idx on search_document using gin (text gin_trgm_ops);
create index search_document_tsv_idx on search_document using gin (tsv);

create table release (
  id uuid primary key default uuidv7(),
  calver text not null unique,
  watermark_changeset_id uuid not null references changeset (id),
  created_at timestamptz not null default now(),
  doi text,
  manifest jsonb
);

select ea_subtype(t::regclass, t::entity_type)
from unnest(array[
  'polity', 'area', 'reporting_unit', 'election', 'contest', 'contest_option', 'result_set',
  'electoral_system', 'party', 'alliance', 'person', 'candidacy', 'body', 'office', 'seat', 'tenure',
  'source'
]) as t;

select ea_version(t::regclass)
from unnest(array[
  'source', 'source_artifact', 'source_page', 'citation', 'polity', 'polity_parent', 'polity_relation',
  'polity_period', 'scheme', 'area', 'polity_territory', 'area_code', 'area_parent', 'area_geometry',
  'boundary_set', 'area_event', 'area_event_member', 'reporting_unit', 'reporting_unit_area', 'crosswalk', 'party',
  'party_color', 'party_affiliation', 'person', 'person_party_membership', 'body', 'body_size',
  'body_event', 'office', 'seat', 'electoral_system', 'election', 'alliance', 'alliance_member',
  'party_relation', 'contest', 'ballot_question', 'contest_option', 'candidacy',
  'candidacy_nomination', 'result_set', 'outcome', 'tenure', 'tenure_event', 'tenure_affiliation',
  'name', 'external_id', 'slug', 'note', 'label', 'flag', 'indicator', 'finance_summary'
]) as t;

insert into channel (id, code, description) values
  (0, 'all', 'All channels combined'),
  (1, 'election_day', 'In person on election day'),
  (2, 'early', 'Early or advance in-person voting'),
  (3, 'postal', 'Postal or mail ballots'),
  (4, 'abroad', 'Votes cast abroad'),
  (5, 'provisional', 'Provisional ballots'),
  (6, 'mobile', 'Mobile ballot boxes'),
  (7, 'prison', 'Prison ballot boxes'),
  (8, 'customs', 'Customs-gate ballot boxes'),
  (9, 'special_rules', 'Special voting rules');

insert into measure (id, code, description) values
  (1, 'votes', 'Votes for an option'),
  (2, 'registered', 'Registered voters'),
  (3, 'ballots_cast', 'Ballots cast'),
  (4, 'valid', 'Valid ballots or votes'),
  (5, 'invalid', 'Invalid ballots'),
  (6, 'blank', 'Blank ballots'),
  (7, 'preference_votes', 'Preference votes within a list'),
  (8, 'list_votes', 'List votes (Swiss party votes)'),
  (9, 'legend_votes', 'Votes for a party or list label only (Brazilian legend votes, Swiss additional votes)'),
  (10, 'candidate_votes', 'Personal votes for a candidate'),
  (11, 'electoral_votes', 'Electoral votes'),
  (12, 'state_delegate_equivalents', 'Iowa caucus state delegate equivalents'),
  (13, 'vote_share', 'Vote share reported without counts'),
  (14, 'voters', 'Voters who signed the roll or were checked in'),
  (15, 'objected', 'Ballots objected to (Turkey: itirazlı)'),
  (16, 'seats', 'Seats won'),
  (17, 'first_alignment', 'Caucus first alignment'),
  (18, 'final_alignment', 'Caucus final alignment'),
  (19, 'apportioned_votes', 'Votes apportioned to this option or contest from a pooled channel by rule'),
  (20, 'present', 'Members present at an assembly vote'),
  (21, 'exhausted', 'Ballots exhausted or non-transferable by the end of a count round'),
  (22, 'unmodified_ballots', 'List ballots cast exactly as printed'),
  (23, 'modified_ballots', 'List ballots changed by hand, or filled in without a printed list');
