import json
from urllib.parse import quote

from ea import raw

SOURCE = "tr.ysk_mayors"
API = "https://tr.wikipedia.org/w/api.php"
YSK = "https://www.ysk.gov.tr/doc/dosyalar/docs/"
RESULTS_2004 = f"{YSK}Mahalli/2004/BelediyeBaskanligi/Pdf/2004Mahalli-BelediyeBsk-Tumu.pdf"
CANDIDATES_2009 = (
    f"{YSK}2009MahalliIdareler/2009ilceBelediyeBsk-AdayListesi.pdf",
    f"{YSK}2009MahalliIdareler/2009BeldeBelediyeBsk-AdayListesi.pdf",
)


def titles(year: int) -> list[str]:
    url = (
        f"{API}?action=query&list=search&srsearch={quote(f'intitle:\"{year} Türkiye yerel seçimleri\"')}"
        "&srlimit=200&srnamespace=0&format=json"
    )
    found = json.loads(raw.cached(SOURCE, url).path.read_bytes())["query"]["search"]
    return sorted(row["title"] for row in found)


def page(title: str) -> str:
    return f"{API}?action=parse&format=json&formatversion=2&prop=wikitext&page={quote(title)}"


def fetch() -> None:
    for year in (2004, 2009):
        search = (
            f"{API}?action=query&list=search&srsearch={quote(f'intitle:\"{year} Türkiye yerel seçimleri\"')}"
            "&srlimit=200&srnamespace=0&format=json"
        )
        raw.fetch(SOURCE, search, suffix=".json")
        for title in titles(year):
            raw.fetch(SOURCE, page(title), suffix=".json")
    raw.fetch(SOURCE, RESULTS_2004, suffix=".pdf")
    for url in CANDIDATES_2009:
        raw.fetch(SOURCE, url, suffix=".pdf")


import re
import subprocess

import polars as pl

from ea.store import DATA

PARSED = DATA / "parsed" / SOURCE
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
PARTIES_2004 = [
    "EMEP", "DSP", "ANAP", "BTP", "AK PARTİ", "BBP", "İP", "ÖDP", "LDP", "TKP", "DYP", "ATP",
    "MİLLET PARTİSİ", "CHP", "GENÇ PARTİ", "YTP", "SHP", "SAADET PARTİSİ", "DP", "MHP", "BAĞIMSIZLAR",
]  # fmt: skip
INTEGER = re.compile(r"^\d{1,3}(?:\.\d{3})*$")


def parse() -> None:
    pdf = raw.cached(SOURCE, RESULTS_2004).path
    html = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    rows: list[dict[str, object]] = []
    for page in html.split("<page ")[1:]:
        words = [(float(a), float(b), float(c), float(d), t) for a, b, c, d, t in WORD.findall(page)]
        heading = [w for w in words if 38 < w[1] < 46 and w[0] < 400]
        province_words = [w[4] for w in sorted(heading, key=lambda w: w[0])]
        if "Çevresi:" not in province_words:
            raise ValueError("page without a seçim çevresi")
        province = " ".join(province_words[province_words.index("Çevresi:") + 1 :])
        vocabulary = {piece for party in PARTIES_2004 for piece in party.split()}
        top = next(w[1] for w in words if w[4] == "Sandık")
        header = sorted((w for w in words if top - 5 < w[1] < top + 22 and w[4] in vocabulary), key=lambda w: w[2])
        edges: dict[str, float] = {}
        cursor = 0
        while cursor < len(header):
            for size in (2, 1):
                pieces = header[cursor : cursor + size]
                label = " ".join(w[4] for w in pieces)
                if label in PARTIES_2004:
                    edges[label] = pieces[-1][2]
                    cursor += len(pieces)
                    break
            else:
                raise ValueError(f"{province}: header word {header[cursor][4]} is no party")
        lines: dict[int, list[tuple[float, float, str]]] = {}
        for x0, y0, x1, _, text in words:
            if y0 > top + 24:
                lines.setdefault(round(y0 / 3), []).append((x0, x1, text))
        labels = [
            (key, line)
            for key, line in sorted(lines.items())
            if all(not (INTEGER.match(t) or re.match(r"^[\d.]+,\d$", t)) for _, _, t in line)
        ]
        for key, line in sorted(lines.items()):
            left = {c: t for _, c, t in line if INTEGER.match(t) and c < 360}
            numbers = [(c, t) for _, c, t in line if INTEGER.match(t)]
            near = sorted(numbers, key=lambda n: n[0])
            columns = [round(c) for c, _ in near]
            cutoff = min(edges.values()) - 35
            if len([c for c in columns if c < cutoff]) != 4:
                continue
            own = sorted((x0, t) for x0, x1, t in line if x1 < 185 and not INTEGER.match(t))
            name = (
                (" ".join(t for _, t in own), own[0][0])
                if own
                else next(
                    ((" ".join(t for _, _, t in label), label[0][0]) for k, label in labels if abs(k - key) <= 2 and label),
                    None,
                )
            )
            if name is None:
                raise ValueError(f"{province}: row without a name at {key}")
            level = "province" if name[1] < 60 else "district" if name[1] < 100 else "town"
            if level == "province":
                province = name[0]
            record: dict[str, object] = {"province": province, "name": name[0], "level": level}
            for (c, t), field in zip([n for n in near if n[0] < cutoff], ("boxes", "registered", "voters", "valid"), strict=True):
                record[field] = int(t.replace(".", ""))
            for c, t in near:
                if c >= cutoff:
                    if not edges:
                        raise ValueError(f"{province}: no party header; band {[(round(w[1]), w[4]) for w in words if 50 < w[1] < 85][:12]}")
                    party = min(edges, key=lambda p: abs(edges[p] - c))
                    if abs(edges[party] - c) > 14:
                        raise ValueError(f"{province} {name[0]}: {t} at {c} is under no party")
                    record[party] = int(t.replace(".", ""))
            rows.append(record)
    frame = pl.DataFrame(rows, infer_schema_length=None).fill_null(0)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "mayors_2004.parquet")
