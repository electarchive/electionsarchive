create type unit_area_method as enum ('source_code', 'city_universe', 'shape_contained');
--> statement-breakpoint
alter table reporting_unit_area add column method unit_area_method;
--> statement-breakpoint
alter table reporting_unit_area_history rename to reporting_unit_area_history_before;
--> statement-breakpoint
create table reporting_unit_area_history (like reporting_unit_area, recorded tstzrange not null, superseded_by uuid not null);
--> statement-breakpoint
insert into reporting_unit_area_history (unit_id, area_id, valid_from, valid_from_precision, valid_to, valid_to_precision, valid,
                                         changeset_id, recorded_from, approximate, partial, recorded, superseded_by)
select unit_id, area_id, valid_from, valid_from_precision, valid_to, valid_to_precision, valid, changeset_id,
       recorded_from, approximate, partial, recorded, superseded_by
from reporting_unit_area_history_before;
--> statement-breakpoint
drop table reporting_unit_area_history_before;
--> statement-breakpoint
create table area_universe (
  area_id uuid not null references area (id),
  valid_from date,
  valid_from_precision date_precision not null default 'day',
  valid_to date,
  valid_to_precision date_precision not null default 'day',
  valid daterange not null generated always as (daterange(valid_from, valid_to, '[)')) stored,
  expected integer not null check (expected > 0),
  method unit_area_method not null,
  source_id uuid references source (id),
  result_set_id uuid references result_set (id),
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  check ((method = 'city_universe') = (result_set_id is not null)),
  primary key (area_id, valid without overlaps)
);
--> statement-breakpoint
select ea_version('area_universe'::regclass);
--> statement-breakpoint
create table rollup_coverage (
  result_set_k integer not null,
  area_k integer not null,
  reporting integer not null,
  expected integer not null,
  primary key (result_set_k, area_k)
);
--> statement-breakpoint
comment on column rollup_coverage.result_set_k is 'references result_set (k)';
--> statement-breakpoint
comment on column rollup_coverage.area_k is 'references area (k)';
