create table statewide_office_year (
  state text not null,
  office_id uuid not null references office (id),
  class text not null,
  year integer not null,
  joint_ticket boolean not null,
  basis text not null,
  primary key (office_id, year)
);
create index statewide_office_year_state_idx on statewide_office_year (state, year);
