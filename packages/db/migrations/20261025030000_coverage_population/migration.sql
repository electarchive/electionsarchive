alter table coverage_government add column population_basis text;
alter table coverage_goal
  add column population_total bigint not null default 0,
  add column population_complete bigint not null default 0,
  add column population_absent bigint not null default 0,
  add column population_estimated bigint not null default 0;
