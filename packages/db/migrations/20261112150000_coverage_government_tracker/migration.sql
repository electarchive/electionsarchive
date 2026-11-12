alter table coverage_government
  add column next_date date,
  add column next_election_id uuid,
  add column cadence_id uuid,
  add column access_id uuid;
