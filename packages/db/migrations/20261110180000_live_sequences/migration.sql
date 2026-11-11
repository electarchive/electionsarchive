select setval(
  pg_get_serial_sequence('live.fetch', 'id'),
  greatest(
    coalesce((select max(id) from live.fetch), 0),
    coalesce(pg_sequence_last_value(pg_get_serial_sequence('live.fetch', 'id')::regclass), 0),
    1
  )
);
select setval(
  pg_get_serial_sequence('live.frame', 'k'),
  greatest(
    coalesce((select max(k) from live.frame), 0),
    coalesce(pg_sequence_last_value(pg_get_serial_sequence('live.frame', 'k')::regclass), 0),
    1
  )
);
select setval(
  pg_get_serial_sequence('live.issue', 'id'),
  greatest(
    coalesce((select max(id) from live.issue), 0),
    coalesce(pg_sequence_last_value(pg_get_serial_sequence('live.issue', 'id')::regclass), 0),
    1
  )
);
