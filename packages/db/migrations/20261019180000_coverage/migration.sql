create table coverage_row (
  path text primary key,
  parent text,
  year integer not null,
  sort integer not null,
  label text not null,
  kind text not null,
  expected integer,
  held integer not null,
  governments integer,
  missing integer,
  county integer,
  precinct integer,
  shapes integer,
  precincts integer not null default 0,
  shaped integer not null default 0
);
create index coverage_row_parent_idx on coverage_row (parent, sort);

create table coverage_government (
  id text primary key,
  state text not null,
  county text,
  kind text not null,
  name text not null,
  function text,
  population integer,
  active boolean not null,
  polity_id uuid,
  period integer,
  phase integer,
  month integer,
  basis text not null
);
create index coverage_government_state_idx on coverage_government (state, kind);
create index coverage_government_polity_idx on coverage_government (polity_id);

create table coverage_government_year (
  government_id text not null references coverage_government (id) on delete cascade,
  year integer not null,
  expected boolean not null,
  held integer not null,
  missing boolean not null,
  precinct integer not null,
  precincts integer not null,
  shaped integer not null,
  primary key (year, government_id)
);

create table coverage_build (
  id boolean primary key default true check (id),
  requested_at timestamptz,
  started_at timestamptz,
  built_at timestamptz
);
insert into coverage_build (requested_at) values (now());
