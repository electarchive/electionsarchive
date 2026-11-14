alter table election_cadence drop constraint election_cadence_period_years_check;
alter table election_cadence add constraint election_cadence_period_years_check check (period_years >= 1 and period_years <= 12);
