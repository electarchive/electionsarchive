create index if not exists area_geometry_label_idx on area_geometry (area_id) include (valid, label_point);
