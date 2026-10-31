create index contest_option_alliance_idx on contest_option (alliance_id, party_id) where alliance_id is not null;
