#!/bin/bash
cd "$HOME/electionsarchive/pipelines" || exit 1
stale=$(psql electionsarchive -tAc "select count(*) from changeset where status = 'published' and published_at > (select coalesce(max(built_at), '-infinity') from contest_cover)")
if [ "$stale" != 0 ] || [ "$(psql electionsarchive -tAc 'select not exists (select 1 from contest_cover)')" = t ]; then
  ../data/logs/gate.sh uv run ea covers || echo "covers failed"
fi
if [ "$(psql electionsarchive -tAc 'select exists (select 1 from election_crown_mark)')" = t ]; then
  ../data/logs/gate.sh uv run ea crowns || echo "crowns failed"
fi
pages=${PAGES_DATABASE_URL:-$(sed -n 's/^PAGES_DATABASE_URL=//p' ../.env)}
keys=${CACHE_KEYS:-$(sed -n 's/^CACHE_KEYS=//p' ../.env)}
if [ -n "$pages" ] && [ -n "$keys" ]; then
  low=$(psql electionsarchive -tAc "select coalesce(min(mark), 0) from polity_record")
  late=$(psql "$pages" -tAc "copy (select key, epoch from bump where epoch > $low) to stdout" |
    psql electionsarchive -tAq -c "create temp table b (key text, epoch bigint)" -c "copy b from stdin" \
      -c "select count(*) from polity_record r where r.mark is null or r.deps is null or exists (select from b where b.epoch > r.mark and (b.key = '*' or b.key = any(r.deps)))")
  unmapped=$(psql electionsarchive -tAc "copy (select id from changeset where status = 'published' and published_at > now() - interval '2 days') to stdout" |
    psql "$pages" -tAq -c "create temp table c (id uuid)" -c "copy c from stdin" -c "select count(*) from c where not exists (select from bumped where changeset_id = c.id)")
  late=$((${late:-1} + ${unmapped:-1}))
else
  late=$(psql electionsarchive -tAc "select count(*) from changeset where status = 'published' and published_at > (select coalesce(min(built_at), '-infinity') from polity_record)")
fi
[ "$late" = 0 ] && { echo "polity records are newer than every publish"; exit 0; }
../data/logs/gate.sh uv run ea records || exit $?
base=${EA_WARM_URL:-http://127.0.0.1:5173}
psql electionsarchive -tAc "select polity_id from polity_record r left join slug s on s.entity_id = r.polity_id and s.is_current and s.path = 'us' order by (s.path is not null) desc, pg_column_size(record) desc limit 12" |
  while read -r id; do
    data=$(printf '{"json":{"id":"%s"}}' "$id" | jq -sRr @uri)
    curl -s -o /dev/null -m 300 -H 'x-ea-refresh: 1' "$base/rpc/polities/record?data=$data" || echo "warm-up of $id failed"
  done
exit 0
