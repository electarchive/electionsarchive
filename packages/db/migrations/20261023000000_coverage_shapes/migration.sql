alter table coverage_goal
  add column unshaped integer not null default 0,
  add column units integer not null default 0,
  add column undrawn integer not null default 0;
