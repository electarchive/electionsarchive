alter table area_geometry_history alter column geom drop not null, add column geom_md5 uuid;
--> statement-breakpoint
update area_geometry_history h
set geom_md5 = md5(st_asbinary(h.geom))::uuid,
    geom = case when exists (
      select from area_geometry g where g.area_id = h.area_id and md5(st_asbinary(g.geom)) = md5(st_asbinary(h.geom))
    ) then null else h.geom end;
--> statement-breakpoint
alter table area_geometry_history alter column geom_md5 set not null;
--> statement-breakpoint
create or replace function area_geometry_capture() returns trigger language plpgsql as $$
begin
  insert into area_geometry_history
  select o.area_id, o.valid_from, o.valid_from_precision, o.valid_to, o.valid_to_precision, o.valid,
         case when exists (
           select from area_geometry g where g.area_id = o.area_id and md5(st_asbinary(g.geom)) = md5(st_asbinary(o.geom))
         ) then null else o.geom end,
         o.label_point, o.quality, o.source_id, o.changeset_id, o.recorded_from,
         tstzrange(o.recorded_from, now()), ea_current_changeset(), md5(st_asbinary(o.geom))::uuid
  from old_rows o;
  return null;
end $$;
