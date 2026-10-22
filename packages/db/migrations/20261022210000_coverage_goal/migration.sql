create table coverage_goal (
  goal text not null,
  state text not null,
  level text not null,
  year integer not null,
  total integer not null,
  complete integer not null,
  absent integer not null,
  bare integer not null,
  totals integer not null,
  partial integer not null,
  coarse integer not null,
  turnout integer not null,
  precincts integer not null,
  shaped integer not null,
  primary key (goal, state, level, year)
);
