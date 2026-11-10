create index if not exists contest_later_round_idx on contest (election_id) where round_number > 1;
