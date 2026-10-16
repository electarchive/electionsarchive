create index reporting_unit_polity_idx on reporting_unit (polity_id);
create index contest_body_idx on contest (body_id);
create index contest_district_area_idx on contest (district_area_id);
create index contest_seat_idx on contest (seat_id);
create index tenure_contest_idx on tenure (contest_id);
create index slug_entity_idx on slug (entity_id);
