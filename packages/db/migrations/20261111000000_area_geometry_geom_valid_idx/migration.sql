create index if not exists area_geometry_geom_valid_idx on area_geometry using gist (geom, valid);
