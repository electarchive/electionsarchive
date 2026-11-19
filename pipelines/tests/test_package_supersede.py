"""Scratch-database test of the default handling when a package's result set supersedes another pipeline's.

Set EA_SCRATCH_DSN to a throwaway database, or leave it unset to start a temporary cluster from the local
PostgreSQL binaries. The test creates only a minimal `result_set` table; it does not need the full schema.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import psycopg
from psycopg import sql

from ea.package import _restore, _supersede

OLD, NEW, CONTEST, ELECTION = (f"00000000-0000-0000-0000-00000000000{n}" for n in (1, 2, 3, 4))
BIN = next(iter(sorted(Path("/usr/lib/postgresql").glob("*/bin"))), None)


def _cluster() -> tuple[str, str | None]:
    dsn = os.environ.get("EA_SCRATCH_DSN")
    if dsn:
        return dsn, None
    if BIN is None:
        raise unittest.SkipTest("no PostgreSQL binaries and no EA_SCRATCH_DSN")
    root = tempfile.mkdtemp()
    os.chmod(root, 0o777)
    run = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    data = f"{root}/data"
    subprocess.run([*run, str(BIN / "initdb"), "-D", data, "-A", "trust"], check=True, capture_output=True)
    subprocess.run(
        [*run, str(BIN / "pg_ctl"), "-D", data, "-w", "-o", f"-p 54329 -k {root}", "start", "-l", f"{root}/log"],
        check=True,
        capture_output=True,
    )
    return f"host={root} port=54329 dbname=postgres user=postgres", root


class Supersede(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn, cls.root = _cluster()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.root and BIN:
            run = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
            subprocess.run(
                [*run, str(BIN / "pg_ctl"), "-D", f"{cls.root}/data", "-m", "immediate", "stop"], check=False
            )
            shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:
        self.conn = psycopg.connect(self.dsn)
        self.conn.execute("drop table if exists result_set, pkg_result_set")
        for table in ("result_set", "pkg_result_set"):
            self.conn.execute(
                sql.SQL(
                    """create table {} (id uuid primary key, election_id uuid, contest_id uuid, stage text,
                       status text, is_default boolean, supersedes_id uuid, changeset_id uuid)"""
                ).format(sql.Identifier(table))
            )
        self.conn.execute("create unique index on result_set (election_id, contest_id, stage) where is_default")

    def tearDown(self) -> None:
        self.conn.close()

    def row(self, table: str, id: str, other: str | None = None, default: bool = True, status: str = "active"):
        self.conn.execute(
            sql.SQL("insert into {} values (%s, %s, %s, 'official', %s, %s, %s, %s)").format(sql.Identifier(table)),
            [id, ELECTION, CONTEST, status, default, other, "00000000-0000-0000-0000-0000000000c1"],
        )

    def state(self, id: str):
        return self.conn.execute("select is_default, status from result_set where id = %s", [id]).fetchone()

    def test_new_default_demotes_the_set_it_supersedes(self) -> None:
        self.row("result_set", OLD)
        self.row("pkg_result_set", NEW, OLD)
        demoted = _supersede(self.conn, {"result_set": ("pkg_result_set", ["id", "supersedes_id"])}, [])
        self.assertEqual(demoted, [OLD])
        self.assertEqual(self.state(OLD), (False, "superseded"))
        self.conn.execute("insert into result_set select * from pkg_result_set")  # the default index accepts it now
        self.assertEqual(self.state(NEW), (True, "active"))

    def test_reload_of_the_older_package_stays_demoted(self) -> None:
        self.row("result_set", OLD, default=False, status="superseded")
        self.row("result_set", NEW, OLD)
        self.row("pkg_result_set", OLD)
        self.assertEqual(_supersede(self.conn, {"result_set": ("pkg_result_set", ["id", "supersedes_id"])}, []), [])
        self.assertEqual(
            self.conn.execute("select is_default, status from pkg_result_set").fetchone(), (False, "superseded")
        )

    def test_restore_reactivates_when_the_successor_is_gone(self) -> None:
        self.conn.execute("drop table if exists changeset")
        self.conn.execute("create table changeset (id uuid, scope jsonb)")
        self.row("result_set", OLD, default=False, status="superseded")
        self.conn.execute(
            "insert into changeset values (gen_random_uuid(), %s::jsonb)",
            ['{"package": "k", "demoted": ["%s"]}' % OLD],
        )
        self.assertEqual(_restore(self.conn, "k", NEW, []), [OLD])
        self.assertEqual(self.state(OLD), (True, "active"))


if __name__ == "__main__":
    unittest.main()
