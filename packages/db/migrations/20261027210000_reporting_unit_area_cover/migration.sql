create index reporting_unit_area_cover_idx on reporting_unit using btree (area_id) include (id);
drop index reporting_unit_area_idx;
alter index reporting_unit_area_cover_idx rename to reporting_unit_area_idx;
