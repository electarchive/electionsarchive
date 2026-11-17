alter type census_world_status add value if not exists 'de_facto';
--> statement-breakpoint
alter type census_world_status add value if not exists 'former';
--> statement-breakpoint
alter type polity_kind add value if not exists 'de_facto_state';
--> statement-breakpoint
alter table census_world
  drop constraint census_world_iso_code_check,
  add constraint census_world_iso_code_check check (iso_code ~ '^[A-Z]{2,3}(-[A-Z0-9]{2,3})?$'),
  add column recognized_within text references census_world (iso_code) deferrable initially deferred,
  add constraint census_world_recognized_within_check check ((status::text = 'de_facto') = (recognized_within is not null)),
  add constraint census_world_recognized_within_distinct_check check (recognized_within is distinct from iso_code);
--> statement-breakpoint
alter table census_world_history add column recognized_within text;
--> statement-breakpoint
create or replace function census_world_capture() returns trigger language plpgsql as $$
begin
  insert into census_world_history (
    iso_code, name_en, status, within_iso, reason, electorate, electorate_date, electorate_precision,
    electorate_source_id, electorate_locator, source_id, locator, changeset_id, recorded_from,
    recorded, superseded_by, recognized_within
  )
  select
    o.iso_code, o.name_en, o.status, o.within_iso, o.reason, o.electorate, o.electorate_date, o.electorate_precision,
    o.electorate_source_id, o.electorate_locator, o.source_id, o.locator, o.changeset_id, o.recorded_from,
    tstzrange(o.recorded_from, now()), ea_current_changeset(), o.recognized_within
  from old_rows o;
  return null;
end $$;
