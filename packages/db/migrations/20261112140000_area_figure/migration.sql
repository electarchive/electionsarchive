create type area_figure_kind as enum ('population');
--> statement-breakpoint
create table area_figure (
  area_id uuid not null references area (id),
  kind area_figure_kind not null,
  date date not null,
  date_precision date_precision not null default 'day',
  value bigint not null check (value > 0),
  source_id uuid not null references source (id),
  locator text,
  changeset_id uuid not null references changeset (id),
  recorded_from timestamptz not null default now(),
  primary key (area_id, kind, date, source_id)
);
--> statement-breakpoint
create index area_figure_changeset_id_idx on area_figure (changeset_id);
--> statement-breakpoint
create index area_figure_source_id_idx on area_figure (source_id);
--> statement-breakpoint
select ea_version('area_figure'::regclass);
