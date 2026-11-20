"""The `result_set.default` check groups defaults by election, contest, and stage, like `result_set_default_idx`."""

import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

import polars as pl

from ea import package

E1, E2 = "00000000-0000-0000-0000-0000000000e1", "00000000-0000-0000-0000-0000000000e2"


class Default(unittest.TestCase):
    def findings(self, rows: list[Mapping[str, str | bool | None]]) -> list[str]:
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(package, "ROOT", tmp / "packages"):
            path = tmp / "packages" / "xx.test" / "build"
            path.mkdir(parents=True)
            (path / "package.json").write_text(
                json.dumps({"key": "xx.test", "pipeline": "xx.test", "depends": [], "external": []})
            )
            schema = package.TABLES["result_set"][1]
            full = [{**dict.fromkeys(schema), **row} for row in rows]
            frame = pl.DataFrame(full, schema=schema)
            frame.write_parquet(path / "result_set.parquet")
            return [code for code, _, _ in package.check(path) if code == "result_set.default"]

    def row(self, n: int, election: str, default: bool = True) -> dict[str, str | bool | None]:
        return {
            "id": f"{n:032d}",
            "election_id": election,
            "contest_id": None,
            "stage": "official",
            "is_default": default,
        }

    def test_one_election_level_default_per_election_passes(self) -> None:
        self.assertEqual(self.findings([self.row(1, E1), self.row(2, E2)]), [])

    def test_two_election_level_defaults_in_one_election_fail(self) -> None:
        self.assertEqual(self.findings([self.row(1, E1), self.row(2, E1)]), ["result_set.default"])
