import re
import unicodedata

import polars as pl


def normalize(precinct: str) -> str:
    plain = unicodedata.normalize("NFKD", precinct).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", plain.upper()).strip()


def code(year: int, state: str, county: str, precinct: str) -> str:
    return f"{year}:{state}:{county}::{normalize(precinct)}"


def expr(year: pl.Expr, state: pl.Expr, county: pl.Expr, precinct: pl.Expr) -> pl.Expr:
    return pl.concat_str(
        year.cast(pl.String),
        pl.lit(":"),
        state,
        pl.lit(":"),
        county,
        pl.lit("::"),
        precinct.str.normalize("NFKD")
        .str.replace_all(r"[^\x00-\x7F]", "")
        .str.to_uppercase()
        .str.replace_all(r"[^A-Z0-9]+", " ")
        .str.strip_chars(),
    )
