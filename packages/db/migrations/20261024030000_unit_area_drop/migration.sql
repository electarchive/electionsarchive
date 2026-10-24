create table unit_area_drop (
  unit_id uuid not null references reporting_unit (id) on delete cascade,
  area_id uuid not null references area (id),
  reason text not null check (reason <> ''),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (unit_id, area_id)
);

select ea_version('unit_area_drop'::regclass);
