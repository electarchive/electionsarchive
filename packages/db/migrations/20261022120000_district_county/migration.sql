create table district_county (
  district_area_id uuid not null,
  district_valid daterange not null,
  county_area_id uuid not null,
  county_valid daterange not null,
  share real not null,
  rest real,
  primary key (district_area_id, district_valid, county_area_id, county_valid)
);
