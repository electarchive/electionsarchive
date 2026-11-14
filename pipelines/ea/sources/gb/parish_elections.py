import html
import re
import subprocess
import sys
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.cc_elections import display
from ea.sources.gb.parish import ENGLISH_GROUPED, fold, slug
from ea.sources.licenses import COUNCIL
from ea.store import DATA, connect

SOURCE = "gb.parish_elections"
VERSION = "1"
WARDS = "gb.parish_ward"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / SOURCE
VOTES = 1
MEASURES = {"electorate": 2, "ballots": 3, "rejected": 5}
COUNCILS: dict[str, dict] = {
    "E07000127": {
        "day": date(2026, 5, 7),
        "publisher": "West Lancashire Borough Council",
        "title": "Parish - West Lancashire Borough Council Elections 2026",
        "url": "https://democracy.westlancs.gov.uk/mgElectionResults.aspx?ID=17&RPID=15632299",
        "inbox": "west-lancashire",
        "reader": "moderngov",
    },
    "E06000006": {
        "day": date(2026, 5, 7),
        "publisher": "Halton Borough Council",
        "title": "Parish - Local Elections",
        "url": "https://moderngov.halton.gov.uk/mgElectionResults.aspx?ID=45&RPID=815205123",
        "inbox": "halton",
        "reader": "moderngov",
    },
    "E07000011": {
        "day": date(2026, 5, 7),
        "publisher": "Huntingdonshire District Council",
        "title": "Town and parish election results 2026",
        "url": "https://huntingdonshire.gov.uk/news/huntingdonshire-town-and-parish-election-results-2026/",
        "files": [
            "https://huntingdonshire.gov.uk/media/ybxjhnid/declaration-of-result-of-poll-parish-7th-may-2026.pdf",
            "https://huntingdonshire.gov.uk/media/umpollv3/notice-of-uncontested-election-town-parish-councils-7-may-2026.pdf",
        ],
        "reader": "notices",
    },
    "E07000177": {
        "day": date(2026, 5, 7),
        "publisher": "Cherwell District Council",
        "title": "Notices of uncontested parish elections, 7 May 2026",
        "url": "https://www.cherwell.gov.uk/download/downloads/id/18690/islip.pdf",
        "files": [
            f"https://www.cherwell.gov.uk/download/downloads/id/{id}/{name}.pdf"
            for id, name in (
                (18711, "barford"),
                (18710, "bletchingdon"),
                (18700, "bloxham"),
                (18715, "broughton"),
                (18717, "charlton-on-otmoor"),
                (18699, "deddington"),
                (18704, "duns-tew"),
                (18707, "epwell"),
                (18698, "fencott-and-murcott"),
                (18716, "finmere"),
                (18709, "fringford"),
                (18706, "hethe"),
                (18702, "horley"),
                (18701, "hornton"),
                (18692, "horton-cum-studley"),
                (18690, "islip"),
                (18691, "kirtlington"),
                (18693, "merton"),
                (18695, "milcombe"),
                (18703, "north-newington"),
                (18708, "shenington-with-alkerton"),
                (18719, "shutford"),
                (18705, "sibford-ferris"),
                (18718, "sibford-gower"),
                (18712, "steeple-aston"),
                (18694, "stratton-audley"),
                (18697, "swalcliffe"),
                (18696, "tadmarton"),
                (18713, "weston-on-the-green"),
                (18714, "wroxton"),
            )
        ],
        "reader": "notices",
    },
}
COUNCILS["E07000177/2026-06"] = {
    "day": date(2026, 6, 18),
    "publisher": "Cherwell District Council",
    "title": "Notice of uncontested election, Shipton-on-Cherwell and Thrupp Parish Council, 18 June 2026",
    "url": "https://www.cherwell.gov.uk/download/downloads/id/18754/notice-of-uncontested-election.pdf",
    "files": ["https://www.cherwell.gov.uk/download/downloads/id/18754/notice-of-uncontested-election.pdf"],
    "reader": "notices",
}
COUNCILS["E06000030"] = {
    "day": date(2026, 5, 7),
    "publisher": "Swindon Borough Council",
    "title": "Parish election results 2026",
    "url": "https://www.swindon.gov.uk/info/20073/elections_and_voting/769/election_results/3",
    "pages": ["https://www.swindon.gov.uk/info/20073/elections_and_voting/769/election_results/3"],
    "reader": "swindon",
}
COUNCILS["E07000220"] = {
    "day": date(2026, 5, 7),
    "publisher": "Rugby Borough Council",
    "title": "2026 elections - parish councils",
    "url": "https://rugby.gov.uk/w/2026-elections-parish-councils",
    "pages": ["https://rugby.gov.uk/w/2026-elections-parish-councils"],
    "files": [
        "https://www.rugby.gov.uk/documents/20124/63178158/Notice+of+uncontested+election+-+Easenhall.pdf/18782199-459c-7524-31df-171a6bd35d3e?t=1775749323133",
        "https://www.rugby.gov.uk/documents/20124/63178158/Notice+of+uncontested+election+-+Shilton+%26+Barnacle.pdf/71863cda-8bb5-15b9-109e-c011889bbad4?t=1775749336972",
        "https://www.rugby.gov.uk/documents/20124/63178158/Notice+of+uncontested+election+-+Wolston.pdf/990b1c59-a4ea-f6f2-7699-c53642e77a47?t=1775749346984",
        "https://www.rugby.gov.uk/documents/20124/63178158/Notice+of+uncontested+election+-+Wolvey.pdf/ec0ffaa0-9947-758b-6e42-e35b34f77995?t=1775749363889",
    ],
    "columns": (290, 290),
    "reader": "notices+rugby",
}
COUNCILS["E07000181"] = {
    "day": date(2026, 5, 7),
    "publisher": "West Oxfordshire District Council",
    "title": "Notice of uncontested election, parish and town councils, 7 May 2026",
    "url": "https://www.westoxon.gov.uk/media/kykluq1i/2026-notice-of-uncontested-election-parish.pdf",
    "files": ["https://www.westoxon.gov.uk/media/kykluq1i/2026-notice-of-uncontested-election-parish.pdf"],
    "reader": "notices",
}
HART = "https://www.hart.gov.uk"
COUNCILS["E07000084"] = {
    "day": date(2026, 5, 7),
    "publisher": "Basingstoke and Deane Borough Council",
    "title": "Notices of uncontested election, parish councils, 7 May 2026",
    "url": "https://www.basingstoke.gov.uk/county-borough-parish-elections",
    "files": [
        f"https://www.basingstoke.gov.uk/content/page/{id}/Notice%20of%20Uncontested%20Election%20-%20{name}.pdf"
        for id, name in (
            (92922, "Ashmansworth"),
            (92923, "Bishops%20Green%20Parish%20Ward"),
            (92924, "Burghclere"),
            (92925, "East%20Woodhay"),
            (92926, "Ecchinswell%20Parish%20Ward"),
            (92927, "Highclere%20Parish%20Ward"),
            (92928, "Newtown"),
            (92929, "Penwood%20Parish%20Ward"),
            (92930, "St%20Mary%20Bourne"),
        )
    ],
    "areas": {
        "Bishops Green": ("Ecchinswell, Sydmonton and Bishops Green", "Bishops Green"),
        "Ecchinswell": ("Ecchinswell, Sydmonton and Bishops Green", "Ecchinswell"),
        "Highclere Parish Ward": ("Highclere", "Highclere"),
        "Penwood Parish Ward": ("Highclere", "Penwood"),
    },
    "reader": "notices",
}
COUNCILS["E06000001"] = {
    "day": date(2026, 5, 7),
    "publisher": "Hartlepool Borough Council",
    "title": "Declaration of result of poll, Elwick Parish, 7 May 2026",
    "url": "https://www.hartlepool.gov.uk/downloads/file/1535/elwick-parish-election-results-7-may-2026",
    "polls": ["https://www.hartlepool.gov.uk/downloads/file/1535/elwick-parish-election-results-7-may-2026"],
    "order": "upper-last",
    "areas": {"ELWICK PARISH": ("Elwick", None)},
    "reader": "declarations",
}
COUNCILS["E07000089"] = {
    "day": date(2026, 5, 7),
    "publisher": "Hart District Council",
    "title": "Notice of uncontested town and parish elections, and the declaration of result for Fleet Town Council, 7 May 2026",
    "url": f"{HART}/voting-and-elections/7-may-2026-election-results",
    "files": [
        f"https://web.archive.org/web/2026id_/{HART}/sites/default/files/2026-04/Notice-of-Uncontested-Town-and-Parish-Elections-2026.pdf"
    ],
    "polls": [f"{HART}/sites/default/files/2026-05/7-may-2026-declaration-results-fleet-town-council-calthorpe.pdf"],
    "order": "upper-first",
    "areas": {
        "Calthorpe": ("Fleet", "Calthorpe"),
        "Fleet Town Council - Ancells": ("Fleet", "Ancells"),
        "Fleet Town Council - Courtmoor": ("Fleet", "Courtmoor"),
        "Fleet Town Council - Fleet Central": ("Fleet", "Fleet Central"),
        "Fleet Town Council - Pondtail": ("Fleet", "Pondtail"),
        "Church Crookham - Church Crookham East": ("Church Crookham", "Church Crookham East"),
        "Church Crookham - Gally Hill": ("Church Crookham", "Gally Hill"),
        "Church Crookham - Queen Elizabeth": ("Church Crookham", "Queen Elizabeth"),
        "Elvetham Heath - Elvetham Heath East": ("Elvetham Heath", "Elvetham Heath East"),
        "Elvetham Heath - Elvetham Heath West": ("Elvetham Heath", "Elvetham Heath West"),
        "Long Sutton & Well": ("Long Sutton", None),
    },
    "reader": "notices+declarations",
}
COUNCILS["E07000071"] = {
    "day": date(2026, 5, 7),
    "publisher": "Colchester City Council",
    "title": "Parish elections 7 May 2026: notices of uncontested election and the East Mersea declaration of result",
    "url": "https://new.colchester.gov.uk/elections/upcoming-elections/parish-elections-7-may-2026",
    "files": [
        "https://cbccrmdata.blob.core.windows.net/noteattachment/CBC-null-Uncontested-Notices-Uncontested%20Notices.pdf"
    ],
    "pages": ["https://new.colchester.gov.uk/elections/2026-election-results/east-mersea-parish-elections-results"],
    "poll": "East Mersea",
    "reader": "colchester+colchester_poll",
}
COUNCILS["E07000118"] = {
    "day": date(2026, 5, 7),
    "publisher": "Chorley Borough Council",
    "title": "Statement of uncontested elections, parish, 7 May 2026",
    "url": "https://chorley.gov.uk/downloads/file/1760/statement-of-uncontested-elections-parish",
    "files": ["https://chorley.gov.uk/downloads/file/1760/statement-of-uncontested-elections-parish"],
    "areas": {
        "EUXTON BUCKSHAW VILLAGE Parish Ward": ("Euxton", "Buckshaw Village"),
        "EUXTON NORTH & EAST Parish Ward": ("Euxton", "North & East"),
        "EUXTON SOUTH Parish Ward": ("Euxton", "South"),
        "HEAPEY Parish": ("Heapey", None),
        "MAWDESLEY Parish": ("Mawdesley", None),
        "WHEELTON Parish": ("Wheelton", None),
        "WHITTLE-LE-WOODS EAST Parish Ward": ("Whittle-le-Woods", "East"),
        "WHITTLE-LE-WOODS WEST Parish Ward": ("Whittle-le-Woods", "West"),
    },
    "reader": "chorley",
}
COUNCILS["E07000086"] = {
    "day": date(2026, 5, 7),
    "publisher": "Eastleigh Borough Council",
    "title": "Parish and town councils: declaration of results, 7 May 2026",
    "url": "https://www.eastleigh.gov.uk/media/16662/parish-and-town-councils-declaration-of-results.pdf",
    "files": ["https://www.eastleigh.gov.uk/media/16662/parish-and-town-councils-declaration-of-results.pdf"],
    "order": "comma",
    "reader": "declarations",
}
CORNWALL_2025 = (
    "https://www.cornwall.gov.uk/the-council-and-democracy/elections/elections-and-referendums/"
    "2025-cornwall-council-and-parish-and-town-council-elections-1-may-2025/"
)
COUNCILS["E06000052"] = {
    "day": date(2025, 5, 1),
    "publisher": "Cornwall Council",
    "title": "Results of 2025 Parish and Town Council elections",
    "url": f"{CORNWALL_2025}results-of-2025-parish-and-town-council-elections/",
    "pages": [
        f"{CORNWALL_2025}results-of-2025-parish-and-town-council-elections/",
        f"{CORNWALL_2025}notices-of-uncontested-election-parish-and-town-council-elections-1-may-2025/",
    ],
    "linked": {
        "files": r"href=['\"](?:https://www\.cornwall\.gov\.uk)?(/media/[^'\"]+/notice-of-uncontested-election-[^'\"]+\.pdf)",
        "polls": r"href=['\"](?:https://www\.cornwall\.gov\.uk)?(/media/[^'\"]+/declaration-of-results?-[^'\"]+\.pdf)",
        "summary": r"href=['\"](?:https://www\.cornwall\.gov\.uk)?(/media/[^'\"]+/summary-of-results-parish-[^'\"]+\.pdf)",
    },
    "columns": (215, 384),
    "order": "upper-first",
    "anchored": True,
    "reader": "notices+declarations",
}
SHROPSHIRE_2025 = (
    "https://elections.shropshire.gov.uk/election-results/town-and-parish-council-elections/"
    "town-and-parish-council-elections-1-may-2025/"
)
COUNCILS["E06000051"] = {
    "day": date(2025, 5, 1),
    "publisher": "Shropshire Council",
    "title": "Town and parish council elections, 1 May 2025: results",
    "url": SHROPSHIRE_2025,
    "pages": [SHROPSHIRE_2025],
    "whole": {"Richards Castle (Shropshire)"},
    "reader": "shropshire",
}
WILTSHIRE = "https://elections.wiltshire.gov.uk"
COUNCILS["E06000054"] = {
    "day": date(2025, 5, 1),
    "publisher": "Wiltshire Council",
    "title": "Town and parish election results: City, Town and Parish Elections 2025, 1 May 2025",
    "url": f"{WILTSHIRE}/Home/Election/284",
    "divisions": f"{WILTSHIRE}/Home/Division/",
    "inbox": "wiltshire",
    "files": [f"{WILTSHIRE}/Home/Document/{document}" for document in (7016, 7029, 7190)],
    "skip": {f"{WILTSHIRE}/Home/Division/8736"},
    "declared": {
        f"{WILTSHIRE}/Home/Division/9024": {
            "file": f"{WILTSHIRE}/Home/Document/7016",
            "votes": {
                "Bailey, Mark Andrew William": (417, True),
                "Evans, Radek Peter": (398, True),
                "King, Carole": (546, True),
                "Stallard, Pauline June": (202, False),
                "Sutton, Michael": (203, True),
                "Ulatowska, Paulina": (326, True),
            },
            "electorate": 3739,
            "ballots": 929,
            "rejected": 44,
        },
        f"{WILTSHIRE}/Home/Division/8999": {
            "file": f"{WILTSHIRE}/Home/Document/7029",
            "votes": {"Alford, Phil": (289, True), "Mitcham, Geoff": (189, False), "Rabey, Saffi": (239, True)},
            "electorate": 2110,
            "ballots": 542,
            "rejected": 12,
        },
        f"{WILTSHIRE}/Home/Division/8959": {
            "file": f"{WILTSHIRE}/Home/Document/7190",
            "votes": {
                "D`Arcy, Stephen John": (183, True),
                "Grant, Gavin": (227, True),
                "Strange, Ruth Eva": (143, False),
            },
            "electorate": 725,
            "ballots": 348,
            "rejected": 3,
        },
    },
    "names": {
        "Scott-Gail Mary Elizabeth": "Mary Elizabeth Scott-Gall",
        "Tester. Matthew Dufton": "Matthew Dufton Tester",
    },
    "reader": "wiltshire",
}
DORSET_2024 = (
    "https://www.dorsetcouncil.gov.uk/w/parish-and-town-councils-within-the-dorset-council-area-elections-on-2-may-2024"
)
COUNCILS["E06000059/2024"] = {
    "day": date(2024, 5, 2),
    "publisher": "Dorset Council",
    "title": "Parish and town councils within the Dorset Council area: elections on 2 May 2024",
    "url": f"{DORSET_2024}-contested",
    "pages": [f"https://web.archive.org/web/20250320152355id_/{DORSET_2024}-contested"],
    "files": [
        "https://www.dorsetcouncil.gov.uk/documents/35024/4507077/NoUE+-+Parish+and+town+areas+-+2+May+2024+v080424.pdf/f33f7ac6-b8a5-a0fb-7d56-0eec6cd07bba"
    ],
    "header": 400,
    "reader": "notices+dorset",
}
COUNCILS["E06000059/2024-06"] = {
    "day": date(2024, 6, 20),
    "publisher": "Dorset Council",
    "title": "Parish councils within the Dorset Council area: elections on 20 June 2024",
    "url": "https://www.dorsetcouncil.gov.uk/w/parish-councils-within-the-dorset-council-area-elections-on-20-june-2024-uncontested-",
    "pages": ["https://www.dorsetcouncil.gov.uk/w/chettle-parish-council-election-on-20-june-2024"],
    "files": [
        "https://www.dorsetcouncil.gov.uk/documents/d/guest/parish-and-town-councils-notices-of-uncontested-elections-20-june-2024-1"
    ],
    "header": 400,
    "ends": ("England,",),
    "reader": "notices+dorset",
}
WARRINGTON = "https://www.warrington.gov.uk"
COUNCILS["E06000007/2024"] = {
    "day": date(2024, 5, 2),
    "publisher": "Warrington Borough Council",
    "title": "Election results 2024: parish council election results, and the notice of uncontested election, parish councils, 2 May 2024",
    "url": f"{WARRINGTON}/election-results-2024",
    "pages": [f"{WARRINGTON}/election-results-2024"],
    "files": [
        f"{WARRINGTON}/sites/default/files/2024-04/Notice%20of%20uncontested%20election%20-%20Parish%20councils%20-%20Election%202%20May%202024.pdf"
    ],
    "polled": {
        "Culcheth and Glazebury": ("Culcheth and Glazebury", "Glazebury & Mee Brow"),
        "Poulton with Fearnhead - Longbarn and Fearnhead": ("Poulton with Fearnhead", "Longbarn & Fearnhead"),
    },
    "reader": "notices+warrington",
}
COUNCILS["E08000018/2024"] = {
    "day": date(2024, 5, 2),
    "publisher": "Rotherham Metropolitan Borough Council",
    "title": "Parish Council Election Results: 2 May 2024, and the notices of uncontested election, parish councils",
    "url": "https://www.rotherham.gov.uk/elections-voting/parish-council-election-result-3-may-2024",
    "pages": [
        "https://www.rotherham.gov.uk/elections-voting/parish-council-election-result-3-may-2024/2",
        "https://www.rotherham.gov.uk/elections-voting/parish-council-election-result-3-may-2024/4",
        "https://www.rotherham.gov.uk/elections-voting/parish-council-election-result-3-may-2024/5",
        "https://www.rotherham.gov.uk/elections-voting/parish-council-election-result-3-may-2024/6",
    ],
    "documents": [
        "https://www.rotherham.gov.uk/downloads/file/3572/aston-cum-aughton-west-ward-of-aston-cum-aughton-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3573/firbeck-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3574/hellaby-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3575/harthill-with-woodall-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3576/dalton-north-ward-of-dalton-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3577/laughton-en-le-morthen-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3578/dalton-east-ward-of-dalton-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3579/dinnington-park-avenue-ward-of-dinnington-st-john-s-town-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3580/letwell-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3581/thrybergh-south-ward-of-thrybergh-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3582/bramley-south-ward-of-bramley-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3583/wickersley-south-ward-of-wickersley-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3584/kiveton-park-ward-of-wales-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3585/treeton-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3586/anston-south-ward-of-anston-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3587/catcliffe-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3588/thorpe-salvin-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3589/orgreave-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3590/aston-cum-aughton-south-ward-of-aston-cum-aughton-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3591/thurcroft-north-ward-of-thurcroft-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3592/bramley-west-ward-of-bramley-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3593/thrybergh-north-ward-of-thrybergh-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3594/todwick-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3595/woodsetts-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3596/wales-ward-of-wales-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3597/anston-north-ward-of-anston-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3598/bramley-north-ward-of-bramley-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3599/maltby-west-ward-of-maltby-town-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3600/wickersley-north-ward-of-wickersley-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3601/brampton-bierlow-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3602/ravenfield-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3603/waverley-community-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3604/thurcroft-south-ward-of-thurcroft-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3605/dalton-south-ward-of-dalton-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3606/aston-cum-aughton-north-ward-of-aston-cum-aughton-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3607/whiston-parish-council-notice-of-uncontested-election",
        "https://www.rotherham.gov.uk/downloads/file/3608/ulley-parish-council-notice-of-uncontested-election",
    ],
    "reader": "rotherham",
}
STROUD = "https://www.stroud.gov.uk/media"
COUNCILS["E07000082/2024"] = {
    "day": date(2024, 5, 2),
    "publisher": "Stroud District Council",
    "title": "Parish election results, and the combined notice of uncontested election, 2 May 2024",
    "url": "https://www.stroud.gov.uk/council-and-democracy/elections/election-results/",
    "files": [f"{STROUD}/2o0dycpl/combined-notice-of-uncontested-election.pdf"],
    "polls": [
        f"{STROUD}/ufweuaaa/cainscross-cainscross-ward-town-council.pdf",
        f"{STROUD}/cvapf220/stroud-trinity-ward-town-council.pdf",
        f"{STROUD}/d3to0c3r/stroud-central-ward-town-council.pdf",
        f"{STROUD}/zzmczucj/stroud-slade-ward-town-council.pdf",
        f"{STROUD}/5gpbhu0g/stonehouse-north-ward-town-council.pdf",
        f"{STROUD}/o3vllwm0/stroud-farmhill-and-paganhill-ward-town-council.pdf",
    ],
    "areas": {
        "Cainscross (Cainscross Ward) Town Council": ("Cainscross", "Cainscross"),
        "Stroud (Trinity Ward) Town Council": ("Stroud", "Trinity"),
        "Stroud (Central Ward) Town Council": ("Stroud", "Central"),
        "Stroud (Slade Ward) Town Council": ("Stroud", "Slade"),
        "Stonehouse (North Ward) Town Council": ("Stonehouse", "North"),
        "Stroud (Farmhill and Paganhill Ward) Town Council": ("Stroud", "Farmhill and Paganhill"),
    },
    "wards": {("Brimscombe and Thrupp", None): "Brimscombe and Thrupp"},
    "ends": ("As", "There"),
    "order": "comma",
    "anchored": "comma",
    "bracketed": True,
    "reader": "notices+declarations",
}
STRATFORD = "https://www.stratford.gov.uk/doc"
COUNCILS["E07000221/2023"] = {
    "day": date(2023, 5, 4),
    "publisher": "Stratford-on-Avon District Council",
    "title": "Notices of uncontested election and declarations of result of poll, town and parish councils, 4 May 2023",
    "url": "https://www.stratford.gov.uk/news/press.cfm/item/137971",
    "files": [f"{STRATFORD}/211878/name/Uncontested%20Parish%20Elections%20May%202023.pdf"],
    "polls": [f"{STRATFORD}/211958/name/Declaration%20of%20Results%20Parish.pdf"],
    "anchored": True,
    "order": "comma",
    "bracketed": True,
    "reader": "notices+declarations",
}
STALBANS = "https://www.stalbans.gov.uk/sites/default/files/attachments"
COUNCILS["E07000240/2023"] = {
    "day": date(2023, 5, 4),
    "publisher": "St Albans City and District Council",
    "title": "Notice of uncontested election and results, parish and town councils, 4 May 2023",
    "url": "https://www.stalbans.gov.uk/node/11309",
    "files": [
        f"{STALBANS}/Parish%20and%20Town%20Council_Notice%20of%20Uncontested%20Election_4%20May%202023_St%20Albans%20City%20and%20District.pdf"
    ],
    "polls": [
        f"{STALBANS}/Results_Parish%20and%20Town%20Council_St%20Albans%20City%20and%20District_4%20May%202023.pdf"
    ],
    "order": "comma",
    "bracketed": True,
    "anchored": True,
    "reader": "notices+declarations",
}
CANTERBURY = "https://www.canterbury.gov.uk/sites/default/files"
COUNCILS["E07000106/2023"] = {
    "day": date(2023, 5, 4),
    "publisher": "Canterbury City Council",
    "title": "Uncontested parish election results 2023, and the Kingston Parish Council declaration of result",
    "url": f"{CANTERBURY}/2023-06/Uncontested%20parish%20election%20results%202023.pdf",
    "files": [f"{CANTERBURY}/2023-06/Uncontested%20parish%20election%20results%202023.pdf"],
    "polls": [f"{CANTERBURY}/2024-04/Kingston%20Parish%20Council%20declaration%20of%20results.pdf"],
    "areas": {"Thanington(North)": ("Thanington", "North")},
    "order": "comma",
    "bracketed": True,
    "reader": "notices+declarations",
}
COUNCILS["E07000220/2023"] = {
    "day": date(2023, 5, 4),
    "publisher": "Rugby Borough Council",
    "title": "Parish council elections 2023",
    "url": "https://rugby.gov.uk/w/parish-council-elections-2023",
    "pages": ["https://rugby.gov.uk/w/parish-council-elections-2023"],
    "files": [
        f"https://www.rugby.gov.uk/documents/d/guest/notice_of_uncontested_election___{name}_parish_council-pdf?download=true"
        for name in (
            "binley_woods",
            "birdingbury",
            "bourton_and_draycote",
            "brandon_and_bretford",
            "cawston",
            "frankton",
            "grandborough",
            "leamington_hastings",
            "marton",
            "princethorpe",
            "ryton_on_dunsmore",
            "stretton_on_dunsmore",
            "thurlaston",
            "willoughby",
        )
    ],
    "reader": "notices+rugby",
}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
POST_NOMINALS = re.compile(r"^(?:[A-Z]{2,5}\.\s+)+")
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PARTIES: dict[str, str | None] = {
    "": None,
    "Independent": "independent",
    "Independent no description": "independent",
    "Reform UK": "Q104721252",
    "Liberal Democrat": "Q9624",
    "Liberal Democrats": "Q9624",
    "Labour Party": "Q9630",
    "Labour": "Q9630",
    "Conservative Party": "Q9626",
    "Conservative Party Candidate": "Q9626",
    "The Conservative Party Candidate": "Q9626",
    "The Green Party candidate": "Q3499324",
    "Conservative": "Q9626",
    "Green Party": "Q3499324",
    "Mebyon Kernow - The Party for Cornwall": "Q161957",
    "Local Conservatives": "Q9626",
    "Conservatives": "Q9626",
    "The Labour Party Candidate": "Q9630",
    "Labour and Co-operative Party": "Q9630",
    "Labour and Co-operative Party Candidate": "Q9630",
    "Green": "Q3499324",
    "Labour and Co-operative": "Q9630",
    "UK Independence Party": "Q10647",
    "Heritage Party": "Q105972889",
    "Homeland Party": "Q119285871",
}
ALIASES = {
    ("E07000082", "Bisley"): "Bisley-with-Lypiatt",
    ("E07000082", "Randwick"): "Randwick and Westrip",
    ("E07000011", "Kimbolton & Stonely"): "Kimbolton",
    ("E07000011", "Leighton Bromswold"): "Leighton",
    ("E07000181", "Asthal"): "Asthall",
    ("E06000052", "Lanteglos-by-Fowey"): "Lanteglos",
    ("E06000052", "St Dominic"): "St. Dominick",
    ("E06000052", "St Michael Penkivel"): "St. Michael Penkevil",
    ("E06000059", "Iwerne Courtney"): "Iwerne Courtney or Shroton",
    ("E08000018", "Anston"): "North and South Anston",
    ("E06000059", "Winfrith Newburgh and East Knighton"): "Winfrith Newburgh",
    ("E07000221", "Newbold Pacey and Ashorne"): "Newbold Pacey",
    ("E07000221", "Whichford and Ascott"): "Whichford",
    ("E07000221", "Warmington and Arlescote"): "Warmington",
    ("E06000051", "Ashford Carbonell"): "Ashford Carbonel",
    ("E06000051", "Clun with Chapel Lawn"): "Clun",
    ("E06000051", "Ellesmere Town"): "Ellesmere Urban",
    ("E06000051", "Leighton and Easton Constantine"): "Leighton and Eaton Constantine",
    ("E06000051", "Ryton and Grindle"): "Ryton",
    ("E06000051", "Wem"): "Wem Urban",
    ("E06000051", "Whitchurch"): "Whitchurch Urban",
    ("E06000054", "Burcombe"): "Burcombe Without",
    ("E06000054", "Ebbesbourne Wake"): "Ebbesborne Wake",
    ("E06000054", "Laverstock and Ford"): "Laverstock",
    ("E06000054", "Stourton"): "Stourton with Gasper",
    ("E06000054", "Clyffe Pypard and Bushton"): "Clyffe Pypard",
    ("E06000054", "Luckington and Alderton"): "Luckington",
    ("E06000054", "Lea, Garsdon & Cleverton"): "Lea and Cleverton",
}
GROUPED = {
    ("E07000071", "Abberton and Langenhoe"),
    ("E07000071", "Winstred Hundred"),
}
CONTEXT = (
    "England's parish and town councils are elected every four years, ward by ward where a parish is warded, by "
    "block vote, on the day of the principal council's elections. Where no more candidates are nominated than there "
    "are seats, those nominated are declared elected without a poll; seats left unfilled are filled by co-option."
)
UNSTATED = (
    "{publisher}'s results list the candidates declared elected unopposed but not how many seats the ward has, "
    "so any seat left unfilled is not recorded."
)


def link(council: dict) -> None:
    for key, pattern in council.get("linked", {}).items():
        origin = re.match(r"https://[^/]+", council["url"])
        assert origin is not None
        council[key] = sorted(
            {
                f"{origin.group(0)}{path}"
                for page in council["pages"]
                for path in re.findall(pattern, Path(raw.cached(SOURCE, page).path).read_text(encoding="utf-8"))
            }
        )


def store(council: dict, url: str) -> None:
    try:
        raw.cached(SOURCE, url)
        return
    except LookupError:
        pass
    response = httpx.get(url, headers={"User-Agent": BROWSER}, timeout=120, follow_redirects=True)
    response.raise_for_status()
    kind = "page" if url in council.get("pages", []) else "docx" if url in council.get("documents", []) else "pdf"
    if (kind == "pdf") != response.content.startswith(b"%PDF") or (kind == "docx") != response.content.startswith(
        b"PK"
    ):
        raise ValueError(f"{url} is not the expected {kind}")
    raw.store(
        SOURCE,
        url,
        response.content,
        suffix={"page": ".html", "docx": ".docx", "pdf": ".pdf"}[kind],
        retrieved_at=datetime.now(UTC),
        media_type={"page": "text/html", "docx": DOCX, "pdf": "application/pdf"}[kind],
    )


def fetch() -> None:
    for council in COUNCILS.values():
        for url in council.get("pages", []):
            store(council, url)
        link(council)
        for url in [
            *council.get("files", []),
            *council.get("polls", []),
            *council.get("summary", []),
            *council.get("documents", []),
        ]:
            store(council, url)
        if "inbox" not in council:
            continue
        for file in sorted((INBOX / council["inbox"]).glob("*.html")):
            content = file.read_bytes()
            url = re.match(rb"<!-- URL: (\S+) -->", content)
            if url is None:
                raise ValueError(f"{file} has no URL header")
            raw.store(
                SOURCE,
                url.group(1).decode(),
                content,
                suffix=".html",
                retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
                media_type="text/html",
            )


def text(cell: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())


def moderngov(lad: str, council: dict) -> list[dict]:
    reads = []
    index = raw._read(SOURCE)
    for url in sorted(
        key for key in index if "mgElectionAreaResults" in key and key.startswith(council["url"].split("/mg")[0])
    ):
        page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
        caption = re.search(r'<caption class="mgSectionTitle">(.*?) - results</caption>', page, re.DOTALL)
        if caption is None:
            raise ValueError(f"{url}: no caption")
        area = text(caption.group(1))
        parish, _, ward = area.partition(" - ")
        table = re.search(r'summary="Table of results[^"]*">(.*?)</table>', page, re.DOTALL)
        assert table is not None
        candidates = []
        for row in re.findall(r'<tr class="mgTable(?:Odd|Even)Row">(.*?)</tr>', table.group(1), re.DOTALL):
            cells = [text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            printed, description, votes, _, outcome = cells
            candidates.append(
                {
                    "printed": printed,
                    "name": printed.replace("`", "'"),
                    "description": description,
                    "votes": None if votes == "No contest" else int(votes.replace(",", "")),
                    "elected": outcome == "Elected",
                }
            )
        voting = re.search(r'summary="Voting summary table">(.*?)</table>', page, re.DOTALL)
        summary = {
            text(label): text(value)
            for label, value in re.findall(
                r"<td[^>]*>([^<]+)</td>\s*<td[^>]*mgAlignRightCell[^>]*>(.*?)</td>",
                voting.group(1) if voting else "",
                re.DOTALL,
            )
        }
        contested = any(candidate["votes"] is not None for candidate in candidates)
        if contested and any(candidate["votes"] is None for candidate in candidates):
            raise ValueError(f"{url}: mixed contest")

        def number(label: str) -> int | None:
            value = summary.get(label)
            return int(value.replace(",", "")) if value else None

        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": None,
                "parish": parish,
                "ward": ward or None,
                "places": number("Seats") if contested else None,
                "electorate": number("Electorate"),
                "ballots": number("Number of ballot papers issued"),
                "rejected": number("Number of ballot papers rejected"),
                "total": number("Total votes"),
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def pdf_pages(url: str) -> list[tuple[str, list[tuple[float, float, str]]]]:
    path = str(raw.cached(SOURCE, url).path)
    layout = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True).stdout
    boxes = subprocess.run(["pdftotext", "-bbox", path, "-"], capture_output=True, text=True, check=True).stdout
    pages = []
    for text_page, box_page in zip(layout.split("\f"), boxes.split("<page ")[1:], strict=False):
        words = [
            (float(x), float(y), html.unescape(word))
            for x, y, word in re.findall(r'xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)</word>', box_page)
        ]
        pages.append((text_page, words))
    return pages


def rows_of(words: list[tuple[float, float, str]], top: float, bottom: float) -> list[list[tuple[float, float, str]]]:
    lines: list[list[tuple[float, float, str]]] = []
    for word in sorted((w for w in words if top < w[1] < bottom), key=lambda w: (w[1], w[0])):
        if lines and word[1] - lines[-1][0][1] < 4:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line) for line in lines]


def joined(line: list[tuple[float, float, str]], low: float, high: float) -> str:
    return " ".join(word for x, _, word in line if low <= x < high)


def surname_first(printed: str) -> str:
    surname, forenames = (part.strip() for part in printed.split(",", 1))
    return f"{forenames} {display(surname)}"


def area_of(page: str) -> tuple[str, str, str | None] | None:
    heading = re.search(
        r"Election of (?:a )?(?:(Parish|Town|City) )?Councillors? for(?: the)?\s*\n\s*((?:.+\n\s*){0,2}?.+?)(?:(?:\s*\n\s*|\s+)on(?=\s*\n|\s+(?:\d|Thursday))|\s*\n\s*(?=Thursday \d))",
        page,
    )
    if heading is None:
        inline = re.search(
            r"Election of (Parish) Councillors for\s+(.+?)\s+Parish\s+Council\s+on Thursday", page, re.DOTALL
        )
        if inline:
            return inline.group(1), " ".join(inline.group(2).split()), None
        return None
    area = " ".join(heading.group(2).split()).replace(" \u2013 ", " - ").replace("`", "'")
    area = re.sub(r"^(?:Parish|Town) of ", "", area)
    area = re.sub(r"(?<=[A-Z]) (?:Parish|Town)$", "", area)
    if area.isupper():
        area = area.title()
    if council := re.fullmatch(r"(.+?) (?:Parish|Town|City|Community) Council(?: - (.+?)(?: Ward)?)?", area):
        return heading.group(1) or "Parish", council.group(1), council.group(2)
    if warded := re.fullmatch(r"(.+?) Parish - (.+?)(?: Ward)?", area):
        return heading.group(1) or "Parish", warded.group(1), warded.group(2)
    area = area.removesuffix(" Parish")
    ward = re.fullmatch(r"(.+?) \((.+)\)", area)
    return heading.group(1) or "Parish", *(
        (ward.group(1), ward.group(2).removesuffix(" Ward")) if ward else (area, None)
    )


def notices(lad: str, council: dict) -> list[dict]:
    reads: list[dict] = []
    for url in council["files"]:
        for number, (page, words) in enumerate(pdf_pages(url), start=1):
            if not page.strip() or "no candidates were nominated" in page:
                continue
            heading = area_of(page)
            if heading is not None and "(surname first)" in page:
                style, parish, ward = heading
                parish, ward = council.get("areas", {}).get(parish, (parish, ward))
                body = page.split("(surname first)")[1].split("Returning Officer")[0]
                lines = [re.split(r"\s{2,}", line)[0] for line in body.split("\n")[1:] if line[:1].strip()][:-1]
                if not lines or len(lines) % 2:
                    raise ValueError(f"{url} page {number}: odd name lines")
                candidates = [
                    {
                        "printed": f"{surname}, {forenames}".replace("`", "'"),
                        "known_as": None,
                        "description": "",
                        "votes": None,
                        "elected": True,
                    }
                    for surname, forenames in zip(lines[::2], lines[1::2], strict=True)
                ]
                reads.append(
                    {
                        "lad": lad,
                        "url": url,
                        "page": number,
                        "style": style,
                        "parish": parish,
                        "ward": ward,
                        "uncontested": False,
                        "places": None,
                        "electorate": None,
                        "ballots": None,
                        "rejected": None,
                        "total": None,
                        "remark": None,
                        "candidates": candidates,
                    }
                )
                continue
            if heading is None:
                if not reads and "UNCONTESTED ELECTIONS" in page:
                    continue
                if not reads or reads[-1]["url"] != url or not reads[-1]["uncontested"]:
                    raise ValueError(f"{url} page {number}: no heading")
                read = reads[-1]
                header = 0.0
            else:
                style, parish, ward = heading
                parish, ward = council.get("areas", {}).get(parish, (parish, ward))
                ward = council.get("wards", {}).get((parish, ward), ward)
                header = max(
                    y for x, y, word in words if word == "Candidate" and x < 260 and y < council.get("header", 300)
                )
                name_limit, description_x = council.get("columns", (215, 375))
                read: dict = {
                    "lad": lad,
                    "url": url,
                    "page": number,
                    "style": style,
                    "parish": parish,
                    "ward": ward,
                    "uncontested": "UNCONTESTED" in page or "Notice of Uncontested Election" in page,
                    "places": None,
                    "electorate": None,
                    "ballots": None,
                    "rejected": None,
                    "total": None,
                    "remark": None,
                    "candidates": [],
                }
                reads.append(read)
            candidates: list[dict] = read["candidates"]
            if read["uncontested"]:
                ends = ("Dated", "Printed", *council.get("ends", ()))
                bottom = min((y - 3 for x, y, word in words if word.startswith(ends)), default=9999.0)
                for line in rows_of(words, header + 6, bottom):
                    name = joined(line, 0, name_limit)
                    description = joined(line, description_x, 999)
                    if re.match(r"[A-Z][A-Z'’ -]*, \S", name):
                        surname, forenames = name.split(",", 1)
                        candidates.append({"surname": surname, "forenames": forenames.strip(), "description": ""})
                    elif name and name == name.upper() and re.search(r"[A-Z]", name):
                        candidates.append({"surname": name, "forenames": "", "description": ""})
                    elif name:
                        if not candidates:
                            raise ValueError(f"{url} page {number}: no surname before {name!r}")
                        candidates[-1]["forenames"] = f"{candidates[-1]['forenames']} {name}".strip()
                    if description:
                        candidates[-1]["description"] = f"{candidates[-1]['description']} {description}".strip()
                continue
            bottom = min(y for x, y, word in words if word.startswith("*"))
            for line in rows_of(words, header + 6, bottom):
                name = joined(line, 0, 250)
                description = joined(line, 250, 470)
                votes = [word for x, _, word in line if x >= 470 and word.isdigit()]
                known = candidates[-1]["known_as"] if candidates else None
                if name.startswith("commonly known as "):
                    candidates[-1]["known_as"] = name.removeprefix("commonly known as ")
                elif name and known is not None and "," not in known:
                    candidates[-1]["known_as"] = f"{known}{'' if known.endswith('-') else ' '}{name}"
                elif re.match(r"[A-Z][A-Z'’ -]*,", name):
                    candidates.append(
                        {"printed": name, "known_as": None, "description": "", "votes": None, "elected": False}
                    )
                elif name:
                    raise ValueError(f"{url} page {number}: stray name {name}")
                if description:
                    candidates[-1]["description"] = f"{candidates[-1]['description']} {description}".strip()
                if votes:
                    candidates[-1]["votes"] = int(votes[0])
                if any(word == "(E)" for x, _, word in line):
                    candidates[-1]["elected"] = True
            figures = re.search(r"Vacant Seats:\s*(\d+)\s+Electorate:\s*(\d+)\s+Ballot Papers Issued:\s*(\d+)", page)
            rejected = re.search(r"\n\s+Total\s+(\d+)\s*\n", page)
            if figures is None or rejected is None:
                raise ValueError(f"{url} page {number}: no figures")
            read |= {
                "places": int(figures.group(1)),
                "electorate": int(figures.group(2)),
                "ballots": int(figures.group(3)),
                "rejected": int(rejected.group(1)),
            }
            lots = re.search(r"(Following an equality of votes.*?\.)\s*\n\s*\n", page, re.DOTALL)
            if lots:
                read["remark"] = " ".join(lots.group(1).split())
            if any(candidate["votes"] is None for candidate in candidates):
                raise ValueError(f"{url} page {number}: a candidate has no votes")
    for read in reads:
        for candidate in read["candidates"]:
            if read.pop("uncontested", None) or "surname" in candidate:
                forenames = candidate.pop("forenames")
                known = re.fullmatch(r"(.+?)\s*\(commonly known as (.+)\)", forenames)
                candidate |= {
                    "printed": f"{candidate.pop('surname')}, {known.group(1) if known else forenames}",
                    "known_as": known.group(2) if known else None,
                    "votes": None,
                    "elected": True,
                }
            candidate["description"] = candidate["description"].replace("`", "'")
            printed = candidate["printed"].replace("`", "'")
            shown = (candidate["known_as"] or printed).replace("`", "'")
            candidate["name"] = surname_first(shown) if "," in shown else shown
            if candidate["known_as"]:
                candidate["printed"] = f"{printed} commonly known as {candidate['known_as']}"
            del candidate["known_as"]
        read.pop("uncontested", None)
    return reads


def count(value: str) -> int:
    return int(value.replace(",", ""))


def swindon(lad: str, council: dict) -> list[dict]:
    url = council["pages"][0]
    page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
    reads = []
    for heading, body in re.findall(r"<h2 id=\"[^\"]+\">(.*?)</h2>(.*?)<hr />", page + "<hr />", re.DOTALL):
        if "<table" not in body:
            continue
        area = text(heading)
        parish, _, ward = area.partition(" Parish \u2013 ")
        candidates = []
        for row in re.findall(r"<tr>\s*<td>(.*?)</tr>", body, re.DOTALL):
            printed, description, votes = (
                text(cell) for cell in re.findall(r"(.*?)</td>\s*(?:<td>|$)", row, re.DOTALL)
            )
            surname = re.match(r"((?:[A-Z][A-Z'\u2019-]*\s+)*[A-Z][A-Z'\u2019-]*)\s+(.+)", printed)
            if surname is None:
                raise ValueError(f"{url}: no surname in {printed}")
            figure = re.fullmatch(r"([\d,]+)( Elected)?", votes)
            if figure is None:
                raise ValueError(f"{url}: no votes in {votes}")
            candidates.append(
                {
                    "printed": printed,
                    "name": f"{POST_NOMINALS.sub('', surname.group(2))} {display(surname.group(1))}",
                    "description": description,
                    "votes": count(figure.group(1)),
                    "elected": figure.group(2) is not None,
                }
            )
        figures = {
            label: count(value)
            for label, value in re.findall(
                r"<li>(Number of ballot papers rejected|Ballot papers issued|Electorate): ([\d,]+)</li>", body
            )
        }
        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": None,
                "parish": parish,
                "ward": ward.removesuffix(" ward") or None,
                "places": sum(1 for candidate in candidates if candidate["elected"]),
                "electorate": figures["Electorate"],
                "ballots": figures["Ballot papers issued"],
                "rejected": figures["Number of ballot papers rejected"],
                "total": None,
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def shropshire(lad: str, council: dict) -> list[dict]:
    url = council["pages"][0]
    page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
    groups = {
        fold(member): label for (key, label), members in ENGLISH_GROUPED.items() if key == lad for member in members
    }
    reads = []
    for heading, body in re.findall(
        r'<div class="result">\s*<h2 class="heading">(.*?)</h2>(.*?)<div class="back-to-top">', page, re.DOTALL
    ):
        area = text(heading)
        found = re.fullmatch(r"(.+?)(?: (?:Town|Parish) Council)? \((.+?)(?: Ward)?\)", area)
        parish, ward = (area, None) if found is None or area in council["whole"] else found.groups()
        parish = re.sub(r" (?:Town|Parish) Council$", "", parish)
        if group := groups.get(fold(parish)):
            parish, ward = group, area if ward else parish
        candidates = []
        for row in re.findall(r'<dl aria-rowindex="\d+" class="row[^"]*" role="row">(.*?)</dl>', body, re.DOTALL):
            cells = dict(re.findall(r'<dd[^>]*data-key="(\w+)" role="cell">(.*?)</dd>', row, re.DOTALL))
            known = re.search(r'<small class="aka">Commonly known as: (.*?)</small>', cells["candidate"])
            printed = text(re.sub(r"<small.*?</small>", "", cells["candidate"])).replace("`", "'")
            used = text(known.group(1)).replace("`", "'") if known else printed
            outcome = text(cells["result"])
            if outcome not in ("Elected", "Uncontested", ""):
                raise ValueError(f"{url}: {area} result {outcome}")
            candidates.append(
                {
                    "printed": printed,
                    "name": person(
                        re.sub(rf"^{re.escape(printed.split()[0])}\b", printed.split()[0], used, flags=re.IGNORECASE),
                        "upper-first",
                    ),
                    "description": text(cells["affiliation"]),
                    "votes": count(text(cells["votes"])) if "votes" in cells else None,
                    "elected": outcome != "",
                }
            )
        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": None,
                "parish": parish,
                "ward": ward,
                "places": sum(1 for candidate in candidates if candidate["elected"]) if "votes" in body else None,
                "electorate": None,
                "ballots": None,
                "rejected": None,
                "total": None,
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def wiltshire(lad: str, council: dict) -> list[dict]:
    groups = {
        fold(member): label for (key, label), members in ENGLISH_GROUPED.items() if key == lad for member in members
    }
    reads = []
    for url in sorted(key for key in raw._read(SOURCE) if key.startswith(council["divisions"])):
        page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
        if f"<a href='{council['url'].removeprefix('https://elections.wiltshire.gov.uk')}'>" not in page:
            raise ValueError(f"{url}: not the {council['day']} election")
        table = re.search(r'<table summary="Candidates for.*?</table>', page, re.DOTALL)
        if table is None or url in council["skip"]:
            continue
        declared = council["declared"].get(url)
        title = re.search(r'<h1 id="title">(.*?)</h1>', page, re.DOTALL)
        seats = re.search(r"Number of seats: (\d+)", page)
        assert title is not None and seats is not None
        area = text(title.group(1))
        found = re.fullmatch(r"(.+?)(?: JPC)? \((.+)\)", area)
        parish, ward = found.groups() if found else (area, None)
        if ward is None and (group := groups.get(fold(parish))):
            parish, ward = group, parish
        candidates = []
        for row in re.findall(r'<tr class="(?:even|odd)[^"]*">(.*?)</tr>', table.group(0), re.DOTALL):
            cells = dict(re.findall(r'<td class="(\w+)">(.*?)</td>', row, re.DOTALL))
            printed = text(cells["nom"])
            votes, elected = (
                declared["votes"][printed]
                if declared
                else (count(text(cells["votes"])), text(cells["elec"]) == "Elected")
                if "votes" in cells
                else (None, True)
            )
            candidates.append(
                {
                    "printed": printed,
                    "name": council["names"].get(printed) or surname_first(printed.replace("`", "'")),
                    "description": text(cells["desc"]),
                    "votes": votes,
                    "elected": elected,
                }
            )

        def figure(pattern: str) -> int | None:
            value = re.search(pattern, page)
            return count(value.group(1)) if value else None

        contested = candidates[0]["votes"] is not None
        figures = declared or {
            "electorate": figure(r"Electorate: ([\d,]+)"),
            "ballots": figure(r"Ballot Papers Issued: ([\d,]+)"),
            "rejected": figure(r'Total spoiled papers:</td><td class="votes">([\d,]+)'),
        }
        reads.append(
            {
                "lad": lad,
                "url": declared["file"] if declared else url,
                "page": None,
                "style": None,
                "parish": parish,
                "ward": ward,
                "places": count(seats.group(1)),
                "electorate": figures["electorate"] if contested else None,
                "ballots": figures["ballots"] if contested else None,
                "rejected": figures["rejected"] if contested else None,
                "total": None,
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def rugby(lad: str, council: dict) -> list[dict]:
    url = council["pages"][0]
    page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
    reads = []
    for heading, body in re.findall(
        r'<h2 class="section_title">([^<]*?) Parish Council</h2>\s*<div class="section_content">(.*?)</article>',
        page,
        re.DOTALL,
    ):
        rows = [
            [text(cell) for cell in re.findall(r"(.*?)</td>\s*(?:<td>|$)", row, re.DOTALL)]
            for row in re.findall(r"<tr>\s*<td>(.*?)</tr>", body, re.DOTALL)
        ]
        if not rows or len(rows[0]) == 1:
            continue
        candidates = []
        for cells in rows:
            printed, description, votes = cells if len(cells) == 3 else (cells[0], "", cells[1])
            figure = re.fullmatch(r"([\d,]+)( elected)?", votes)
            if figure is None:
                raise ValueError(f"{url}: no votes in {votes}")
            candidates.append(
                {
                    "printed": printed,
                    "name": surname_first(printed),
                    "description": description,
                    "votes": count(figure.group(1)),
                    "elected": figure.group(2) is not None,
                }
            )
        figures = {
            label: count(value)
            for label, value in re.findall(r"(Vacant seats|Electorate|Ballot papers issued): ([\d,]+)", text(body))
        }
        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": "Parish",
                "parish": text(heading),
                "ward": None,
                "places": figures.get("Vacant seats", sum(bool(candidate["elected"]) for candidate in candidates)),
                "electorate": figures.get("Electorate"),
                "ballots": figures.get("Ballot papers issued"),
                "rejected": None,
                "total": None,
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def dorset(lad: str, council: dict) -> list[dict]:
    url = council["pages"][0]
    page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
    reads = []
    for heading, body in re.findall(r"<h[23]>(.*?)</h[23]>(.*?)(?=<h[23]>|$)", page, re.DOTALL):
        area = re.fullmatch(r"(.+?)\s*\((\d+) seats?\)|Results of the (.+?) election on .+", text(heading))
        if area is None or "<table" not in body:
            continue
        council_name, _, ward = (area.group(1) or area.group(3)).partition(" - ")
        parish = re.sub(r" (?:Parish|Town)(?: Council)?$", "", council_name)
        tables = re.findall(r"<tbody>(.*?)</tbody>", body, re.DOTALL)
        candidates = []
        for row in re.findall(r"<tr>(.*?)</tr>", tables[0], re.DOTALL):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            printed, description, votes = (text(cell).replace("`", "'") for cell in cells)
            known = re.search(r"\(commonly known as (.+)\)$", printed)
            printed = re.sub(r"\s*\(commonly known as .+\)$", "", printed)
            surname = re.fullmatch(r"((?:[A-Z][A-Z'\u2019-]+\s+)*[A-Z][A-Z'\u2019-]+)\s+(\S.*)", printed)
            figure = re.fullmatch(r"(\d+)( ELECTED)?", votes)
            if surname is None or figure is None:
                raise ValueError(f"{url}: cannot read {printed} {votes}")
            candidates.append(
                {
                    "printed": f"{printed} commonly known as {known.group(1)}" if known else printed,
                    "name": known.group(1) if known else f"{surname.group(2)} {display(surname.group(1))}",
                    "description": description,
                    "votes": int(figure.group(1)),
                    "elected": figure.group(2) is not None,
                }
            )
        rejected = re.search(r"<td>Total</td>\s*<td>(\d+)</td>", tables[1])
        figures = dict(re.findall(r"(vacant seats|electorate|ballot papers issued): (\d+)", text(body)))
        if rejected is None or int(figures["vacant seats"]) != int(area.group(2) or figures["vacant seats"]):
            raise ValueError(f"{url}: {council_name} figures")
        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": "Town" if "Town Council" in council_name else "Parish",
                "parish": parish,
                "ward": ward.removesuffix(" Ward") or None,
                "places": int(figures["vacant seats"]),
                "electorate": int(figures["electorate"]),
                "ballots": int(figures["ballot papers issued"]),
                "rejected": int(rejected.group(1)),
                "total": None,
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def warrington(lad: str, council: dict) -> list[dict]:
    url = council["pages"][0]
    page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
    section = page.split("<h2>Parish council election results</h2>")[1].split("Police and Crime Commissioner")[0]
    reads = []
    for heading, body in re.findall(r"<h3>(.*?)</h3>(.*?)(?=<h3>|$)", section, re.DOTALL):
        area = re.fullmatch(r"(.+?) (?:Parish|Town) Council(?: - (.+?))? \(tu?r?nout [\d.]+%\)", text(heading))
        if area is None:
            raise ValueError(f"{url}: cannot read {text(heading)}")
        printed = f"{area.group(1)} - {area.group(2)}" if area.group(2) else area.group(1)
        parish, ward = council["polled"].get(printed, (area.group(1), area.group(2)))
        candidates = []
        lots = []
        for line in re.findall(r"<p>(.*?)</p>", body, re.DOTALL):
            found = re.fullmatch(
                r"(.+?)(?:, (.+?))? - ([\d,]+)(?: votes)?( \(elected(?: - via drawn lot)?\))?", text(line)
            )
            if found is None:
                raise ValueError(f"{url}: cannot read {text(line)}")
            if found.group(4) and "lot" in found.group(4):
                lots.append(found.group(1))
            candidates.append(
                {
                    "printed": found.group(1),
                    "name": found.group(1),
                    "description": found.group(2) or "",
                    "votes": count(found.group(3)),
                    "elected": found.group(4) is not None,
                }
            )
        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": None,
                "parish": parish,
                "ward": ward,
                "places": sum(1 for candidate in candidates if candidate["elected"]),
                "electorate": None,
                "ballots": None,
                "rejected": None,
                "total": None,
                "remark": f"{' and '.join(lots)} elected after drawing lots." if lots else None,
                "candidates": candidates,
            }
        )
    return reads


def rotherham_area(heading: str) -> tuple[str, str | None]:
    heading = re.sub(r" on Thursday .+$", "", " ".join(heading.replace("`", "'").split()))
    found = re.fullmatch(r"(?:(.+?) Ward of )?(.+?) (?:Town Parish|Parish|Town|Community) Council", heading)
    if found is None:
        raise ValueError(f"cannot read {heading}")
    return found.group(2), found.group(1)


def rotherham(lad: str, council: dict) -> list[dict]:
    reads = []
    for url in council["documents"]:
        document = zipfile.ZipFile(raw.cached(SOURCE, url).path).read("word/document.xml").decode()

        def paragraphs(xml: str) -> list[str]:
            found = (
                html.unescape("".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", part)))
                for part in re.findall(r"<w:p[ >].*?</w:p>", xml, re.DOTALL)
            )
            return [" ".join(line.split()) for line in found if line.strip()]

        declaration = next(line for line in paragraphs(document) if line.startswith("I, "))
        if "no candidates were nominated" in declaration:
            continue
        area = re.search(r"duly elected (?:a )?(?:Parish )?Councillors? (?:for|to) (.+?)\.?$", declaration)
        if area is None:
            raise ValueError(f"{url}: cannot read {declaration}")
        parish, ward = rotherham_area(area.group(1))
        candidates = []
        for row in re.findall(r"<w:tr[ >].*?</w:tr>", document, re.DOTALL)[1:]:
            cells = [paragraphs(cell) for cell in re.findall(r"<w:tc>.*?</w:tc>", row, re.DOTALL)]
            if len(cells[0]) != 2:
                raise ValueError(f"{url}: cannot read {cells[0]}")
            surname, forenames = cells[0]
            candidates.append(
                {
                    "printed": f"{surname}, {forenames}",
                    "name": f"{forenames} {display(surname)}",
                    "description": " ".join(cells[2]),
                    "votes": None,
                    "elected": True,
                }
            )
        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": None,
                "parish": parish,
                "ward": ward,
                "places": None,
                "electorate": None,
                "ballots": None,
                "rejected": None,
                "total": None,
                "remark": None,
                "candidates": candidates,
            }
        )
    for url in council["pages"]:
        page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
        heading = re.search(r"Election of a Borough Councillor for(.+?) on Thursday", text(page))
        table = re.search(r"<tbody>(.*?)</tbody>", page, re.DOTALL)
        if heading is None or table is None:
            raise ValueError(f"{url}: no result")
        parish, ward = rotherham_area(heading.group(1))
        body = text(page)
        declared = re.search(r"And I do hereby declare that (.+?) are duly elected", body)
        figures = re.search(
            r"Total (\d+) Vacant Seats: (\d+) Electorate: ([\d,]+) Ballot Papers Issued: ([\d,]+)", body
        )
        if declared is None or figures is None:
            raise ValueError(f"{url}: no declaration")
        elected = {fold(name.replace("\u2019", "'")) for name in re.split(r", | and ", declared.group(1))}
        candidates = []
        for row in re.findall(r"<tr>(.*?)</tr>", table.group(1), re.DOTALL):
            cells = [text(cell).replace("`", "'") for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            printed, _, known = cells[0].partition(" commonly known as ")
            name = surname_first(printed)
            candidates.append(
                {
                    "printed": f"{printed} commonly known as {known}" if known else printed,
                    "name": surname_first(known) if known else name,
                    "description": cells[1],
                    "votes": count(cells[2]),
                    "elected": fold(name) in elected,
                }
            )
        if sum(1 for candidate in candidates if candidate["elected"]) != len(elected) or len(elected) != int(
            figures.group(2)
        ):
            raise ValueError(f"{url}: elected names do not match")
        reads.append(
            {
                "lad": lad,
                "url": url,
                "page": None,
                "style": None,
                "parish": parish,
                "ward": ward,
                "places": int(figures.group(2)),
                "electorate": count(figures.group(3)),
                "ballots": count(figures.group(4)),
                "rejected": int(figures.group(1)),
                "total": None,
                "remark": None,
                "candidates": candidates,
            }
        )
    return reads


def person(printed: str, order: str) -> str:
    if order == "comma":
        return surname_first(printed)
    if order == "upper-first":
        found = re.fullmatch(
            r"((?:[A-Z][A-Z\u00c0-\u00de'\u2019-]+\s+)*[A-Z][A-Z\u00c0-\u00de'\u2019-]+)\s+(\S.*)", printed
        )
        if found is None:
            raise ValueError(f"no surname in {printed}")
        return f"{found.group(2)} {display(found.group(1))}"
    found = re.fullmatch(r"(\S.*?)\s+((?:[A-Z][A-Z'\u2019-]+\s?)+)", printed)
    if found is None:
        raise ValueError(f"no surname in {printed}")
    return f"{found.group(1)} {display(found.group(2).strip())}"


def anchored(words: list[tuple[float, float, str]], comma: bool = False) -> list[dict]:
    top = next(y for x, y, word in words if word == "Votes*")
    bottom = min(y for x, y, word in words if word == "*" and y > top)
    inside = [(x, y, word) for x, y, word in words if top + 4 < y < bottom - 2]
    anchors = sorted(y for x, y, word in inside if 440 <= x < 510 and re.fullmatch(r"[\d,]+", word))
    cells: list[dict[str, list[tuple[float, float, str]]]] = [{"name": [], "description": []} for _ in anchors]
    elected = [False for _ in anchors]
    if comma:
        starts = [
            line[0][1] for line in rows_of([w for w in inside if w[0] < 250], 0, 9999) if "," in joined(line, 0, 250)
        ]
        if len(starts) != len(anchors):
            raise ValueError(f"{len(starts)} names for {len(anchors)} vote counts")
    for x, y, word in inside:
        nearest = min(range(len(anchors)), key=lambda i: abs(anchors[i] - y))
        if x >= 510 and word in ("E", "Elected"):
            elected[nearest] = True
        elif x < 250 and comma:
            cells[max(i for i, start in enumerate(starts) if start <= y + 2)]["name"].append((y, x, word))
        elif x < 440:
            cells[nearest]["name" if x < 250 else "description"].append((y, x, word))
    return [
        {
            "printed": " ".join(word for _, _, word in sorted(cell["name"])).replace("`", "'"),
            "description": " ".join(word for _, _, word in sorted(cell["description"])),
            "votes": count(next(word for x, y, word in inside if y == anchor and 440 <= x < 510)),
            "elected": chosen,
        }
        for cell, anchor, chosen in zip(cells, anchors, elected, strict=True)
    ]


def declarations(lad: str, council: dict) -> list[dict]:
    order = council["order"]
    reads: list[dict] = []
    for url in council["polls"] if "polls" in council else council["files"]:
        for number, (page, words) in enumerate(pdf_pages(url), start=1):
            lines = page.split("\n")
            if not any("DECLARATION OF RESULT OF POLL" in line for line in lines):
                continue
            start = next(i for i, line in enumerate(lines) if "Election of" in line)
            owner = next(line.strip() for line in reversed(lines[:start]) if line.strip())
            heading = []
            for line in lines[start + 1 :]:
                if not line.strip() or line.strip().startswith("on "):
                    if heading:
                        break
                    continue
                if line.strip() != "for":
                    heading.append(line.strip())
            area = " ".join(heading)
            suffixed = re.fullmatch(r"(.+?) (?:Parish|Town|City|Community) Council(?: - (.+) Ward)?", area)
            if area in council.get("areas", {}):
                parish, ward = council["areas"][area]
            elif suffixed:
                parish, ward = suffixed.groups()
            elif council.get("bracketed"):
                bracketed = re.fullmatch(r"(.+?) ?\((.+)\)", area)
                parish, ward = bracketed.groups() if bracketed else (area, None)
            else:
                body = re.fullmatch(r"(.+) (?:Town|Parish) Council", owner)
                if body is None:
                    raise ValueError(f"{url} page {number}: no area for {owner} / {area}")
                parish, ward = body.group(1), area.removesuffix(" Ward")
            parish, ward = parish.replace("\u2019", "'"), ward and ward.replace("\u2019", "'")
            top = next(i for i, line in enumerate(lines) if "Votes*" in line)
            end = next(i for i, line in enumerate(lines) if line.startswith("* If elected"))
            candidates: list[dict] = anchored(words, council["anchored"] == "comma") if council.get("anchored") else []
            block = [] if council.get("anchored") else [line for line in lines[top + 1 : end] if line.strip()]
            margin = min((len(line) - len(line.lstrip()) for line in block), default=0)
            for line in block:
                cells = re.split(r"\s{2,}", line.strip())
                column_zero = not line[margin].isspace()
                rest = "  ".join(cells[1:] if column_zero else cells)
                if column_zero:
                    if candidates and (
                        cells[0][0].islower()
                        or cells[0].lower().startswith("commonly known as")
                        or candidates[-1]["printed"].endswith(" commonly known as")
                        or (order == "comma" and "," not in cells[0])
                    ):
                        candidates[-1]["printed"] += f" {cells[0]}"
                    else:
                        printed = cells[0].replace("`", "'")
                        candidates.append({"printed": printed, "description": "", "votes": None, "elected": False})
                if rest:
                    figure = re.fullmatch(r"(?:(.*?)\s{2,})?(Elected\s+)?([\d,]+)(?:\s+(E|Elected))?", rest)
                    if figure is None or not candidates:
                        raise ValueError(f"{url} page {number}: cannot read {line.strip()!r}")
                    candidates[-1] |= {
                        "description": figure.group(1) or "",
                        "votes": count(figure.group(3)),
                        "elected": figure.group(2) is not None or figure.group(4) is not None,
                    }
            declared = re.search(r"declare that(.*?)(?:is|are) duly elected", page, re.DOTALL)
            marked = any(c["elected"] for c in candidates)
            for candidate in candidates:
                found = re.fullmatch(r"(.*?)\s*\(?commonly known as:?\s*(.*?)\)?", candidate["printed"], re.IGNORECASE)
                main, known = found.groups() if found else (candidate["printed"], None)
                if not marked and declared and main in " ".join(declared.group(1).split()):
                    candidate["elected"] = True
                candidate["name"] = person((known or main).replace("\u2019", "'"), order)
                if known:
                    candidate["printed"] = f"{main} commonly known as {known}"
            figures = re.search(
                r"Vacant Seats:\s*(\d+)\s+Electorate:\s*([\d,]+)\s+Ballot Papers Issued:\s*([\d,]+)", page
            )
            rejected = re.search(r"\n\s+Total\s+(\d+)\s*\n", page)
            reasons = [int(n) for n in re.findall(r"\n\s*[A-E]\s{2,}\D+?\s{2,}(\d+)\s*(?=\n)", page)]
            if rejected and len(reasons) == 5 and sum(reasons) != int(rejected.group(1)):
                rejected = None
            places = int(figures.group(1)) if figures else sum(c["elected"] for c in candidates)
            if places != sum(c["elected"] for c in candidates):
                raise ValueError(f"{url} page {number}: {places} seats, other elected count")
            reads.append(
                {
                    "lad": lad,
                    "url": url,
                    "page": number,
                    "style": None,
                    "parish": parish,
                    "ward": ward,
                    "places": places,
                    "electorate": count(figures.group(2)) if figures else None,
                    "ballots": count(figures.group(3)) if figures else None,
                    "rejected": int(rejected.group(1)) if rejected else None,
                    "total": None,
                    "remark": None,
                    "candidates": candidates,
                }
            )
    return reads


def chorley(lad: str, council: dict) -> list[dict]:
    reads: list[dict] = []
    for url in council["files"]:
        for number, (page, _) in enumerate(pdf_pages(url), start=1):
            heading = re.search(
                r"Election of Parish Councillors to(?: Parish Council)?\s*\n\s*(.+?)\s*\n\s*ON Thursday", page
            )
            if heading is None:
                continue
            parish, ward = council["areas"][heading.group(1)]
            body = page.split("Names of persons elected")[1].split("If a candidate")[0].split("\n")[1:]
            candidates: list[dict] = []
            for line in body:
                if not line[:3].strip():
                    continue
                cell = re.split(r"\s{2,}", line.strip())[0]
                if cell.startswith("(Known as"):
                    candidates[-1]["known"] = cell.removeprefix("(Known as ").removesuffix(")")
                elif line[0] == " " and line[1] != " ":
                    candidates.append({"printed": cell, "known": None})
            if not candidates:
                raise ValueError(f"{url} page {number}: no names")
            reads.append(
                {
                    "lad": lad,
                    "url": url,
                    "page": number,
                    "style": "Parish",
                    "parish": parish,
                    "ward": ward,
                    "places": None,
                    "electorate": None,
                    "ballots": None,
                    "rejected": None,
                    "total": None,
                    "remark": None,
                    "candidates": [
                        {
                            "printed": (
                                f"{c['printed']} commonly known as {c['known']}" if c["known"] else c["printed"]
                            ),
                            "name": c["known"] or surname_first(c["printed"]),
                            "description": "",
                            "votes": None,
                            "elected": True,
                        }
                        for c in candidates
                    ],
                }
            )
    return reads


def colchester(lad: str, council: dict) -> list[dict]:
    reads: list[dict] = []
    for url in council["files"]:
        parish = ward = None
        for number, (page, words) in enumerate(pdf_pages(url), start=1):
            heading = re.search(r"Election of Parish Councillors to\s*\n\s*(.+?) Parish Council\s*\n", page)
            if heading:
                parish, ward = " ".join(heading.group(1).split()), None
            elif parish is None:
                continue
            bottom = min((y for x, y, word in words if word == "Dated"), default=9999.0)
            read: dict | None = None
            pending: str | None = None
            started = False
            for line in rows_of(words, 0, bottom):
                label = " ".join(word for _, _, word in line)
                name = joined(line, 0, 215)
                if label.startswith("Name of Candidate"):
                    started = True
                    ward = pending or ward
                    pending = None
                    read = next(
                        (r for r in reads if r["parish"] == parish and r["ward"] == ward and r["url"] == url), None
                    )
                    if read is None:
                        read = {
                            "lad": lad,
                            "url": url,
                            "page": number,
                            "style": "Parish",
                            "parish": parish,
                            "ward": ward,
                            "places": None,
                            "electorate": None,
                            "ballots": None,
                            "rejected": None,
                            "total": None,
                            "remark": None,
                            "candidates": [],
                        }
                        reads.append(read)
                    continue
                if not name and label.endswith(" Ward"):
                    pending = label.removesuffix(" Ward")
                    started = False
                    continue
                if not started or read is None:
                    continue
                candidates = read["candidates"]
                description = joined(line, 380, 999)
                if re.fullmatch(r"[A-Z][A-Z'\u2019 -]*", name):
                    candidates.append({"surname": name, "forenames": "", "description": ""})
                elif name:
                    candidates[-1]["forenames"] = f"{candidates[-1]['forenames']} {name}".strip()
                if description:
                    candidates[-1]["description"] = f"{candidates[-1]['description']} {description}".strip()
    for read in reads:
        for candidate in read["candidates"]:
            candidate["printed"] = f"{candidate.pop('surname')}, {candidate.pop('forenames')}"
            candidate["name"] = surname_first(candidate["printed"])
            candidate |= {"votes": None, "elected": True}
    return reads


def colchester_poll(lad: str, council: dict) -> list[dict]:
    url = council["pages"][0]
    page = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8")
    body = page.split("<tbody>")[1].split("</tbody>")[0]
    candidates = []
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL):
        name, description, votes = (text(cell) for cell in re.findall(r"<td>(.*?)</td>", row, re.DOTALL))
        found = re.fullmatch(r"(.*?) commonly known as (.*)", name)
        printed, known = found.groups() if found else (name, None)
        figure = re.fullmatch(r"([\d,]+)( Elected)?", votes)
        if figure is None:
            raise ValueError(f"{url}: no votes in {votes}")
        candidates.append(
            {
                "printed": f"{printed} commonly known as {known}" if known else printed,
                "name": surname_first(known or printed),
                "description": description,
                "votes": count(figure.group(1)),
                "elected": figure.group(2) is not None,
            }
        )
    figures = dict(re.findall(r"(Vacant seats|Electorate|Ballot papers issued): ([\d,]+)", text(page)))
    rejected = re.search(r"<strong>Total</strong></td>\s*<td>(\d+)", page)
    return [
        {
            "lad": lad,
            "url": url,
            "page": None,
            "style": "Parish",
            "parish": council["poll"],
            "ward": None,
            "places": count(figures["Vacant seats"]),
            "electorate": count(figures["Electorate"]),
            "ballots": count(figures["Ballot papers issued"]),
            "rejected": int(rejected.group(1)) if rejected else None,
            "total": None,
            "remark": None,
            "candidates": candidates,
        }
    ]


def summarized(url: str) -> dict[tuple[str, str | None], dict]:
    sections: dict[tuple[str, str | None], dict] = {}
    lines = "\n".join(page for page, _ in pdf_pages(url)).split("\n")
    for start, line in enumerate(lines):
        if "Name of Candidate" not in line:
            continue
        title = re.fullmatch(r"(.+?) (?:Parish|Town|City|Community) Council(?: - (.+) Ward)?", lines[start - 1].strip())
        if title is None:
            raise ValueError(f"{url}: no council in {lines[start - 1]!r}")
        end = next(i for i in range(start, len(lines)) if "Vacant Seats:" in lines[i])
        figures = re.search(
            r"Vacant Seats:\s*(\d+)\s+Electorate:\s*([\d,]+)(?:\s+Ballot Papers Issued:\s*([\d,]+))?", lines[end]
        )
        assert figures is not None
        votes = [re.search(r"\s{2,}([\d,]+)(?:\s+Elected)?$", line) for line in lines[start + 1 : end]]
        sections[title.group(1), title.group(2)] = {
            "places": int(figures.group(1)),
            "electorate": count(figures.group(2)),
            "ballots": count(figures.group(3)) if figures.group(3) else None,
            "votes": sorted(count(vote.group(1)) for vote in votes if vote and vote.start(1) > 95),
        }
    return sections


def reconcile(lad: str, council: dict, reads: list[dict]) -> None:
    sections = {key: value for url in council.get("summary", []) for key, value in summarized(url).items()}
    if not sections:
        return
    if len(sections) != len(reads):
        raise ValueError(f"{lad}: {len(sections)} summary sections, {len(reads)} reads")
    for read in reads:
        section = sections[read["parish"], read["ward"]]
        votes = sorted(candidate["votes"] for candidate in read["candidates"] if candidate["votes"] is not None)
        if votes != section["votes"]:
            raise ValueError(f"{read['url']}: votes differ from the summary")
        for field in ("places", "electorate", "ballots"):
            if read[field] is not None and read[field] != section[field]:
                raise ValueError(f"{read['url']}: {field} {read[field]} but the summary has {section[field]}")
            read[field] = section[field]


READERS = {
    "moderngov": moderngov,
    "notices": notices,
    "swindon": swindon,
    "rugby": rugby,
    "shropshire": shropshire,
    "wiltshire": wiltshire,
    "declarations": declarations,
    "chorley": chorley,
    "colchester": colchester,
    "colchester_poll": colchester_poll,
    "dorset": dorset,
    "warrington": warrington,
    "rotherham": rotherham,
}


def parse() -> None:
    for council in COUNCILS.values():
        link(council)
    with connect() as conn:
        parishes = [
            row
            for day in sorted({council["day"] for council in COUNCILS.values()})
            for row in conn.execute(
                """select pc.code, n.text, pe.value, a.scheme_code, c.code
               from area a join area_code c on c.area_id = a.id
               join area_parent p on p.area_id = a.id join area_code pc on pc.area_id = p.parent_id
               join name n on n.entity_id = a.id and n.lang = 'en'
               join polity_territory pt on pt.area_id = a.id and pt.valid @> %s::date
               join external_id pe on pe.entity_id = pt.polity_id and pe.scheme = 'gb.polity'
               where a.scheme_code in ('gb.parish', 'gb.parish_group') and pc.code = any(%s)""",
                [day, sorted({entry.split("/")[0] for entry, council in COUNCILS.items() if council["day"] == day})],
            ).fetchall()
        ]
    known: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
    for lad, name, key, scheme, code in parishes:
        known.setdefault((lad, fold(name)), set()).add((key, scheme, code))
    rows = []
    misses: list[str] = []
    for entry, council in COUNCILS.items():
        reads = [read for reader in council["reader"].split("+") for read in READERS[reader](entry, council)]
        reconcile(entry, council, reads)
        lad = entry.split("/")[0]
        for read in reads:
            if (lad, read["parish"]) in GROUPED:
                continue
            found = known.get((lad, fold(ALIASES.get((lad, read["parish"]), read["parish"]))), set())
            if len(found) != 1:
                misses.append(f"{lad} {read['parish']}: {len(found)} parishes")
                continue
            key, scheme, code = next(iter(found))
            for position, candidate in enumerate(read.pop("candidates"), start=1):
                rows.append(
                    {
                        **read,
                        "key": key,
                        "scheme": scheme,
                        "code": code,
                        "day": council["day"],
                        "number": position,
                        **candidate,
                    }
                )
    if misses:
        raise ValueError("; ".join(misses))
    frame = pl.DataFrame(rows, infer_schema_length=None)
    contests = frame.select("key", "day", "ward").unique().height
    uncontested = frame.filter(pl.col("votes").is_null()).select("key", "day", "ward").unique().height
    print(
        f"{frame['key'].n_unique()} councils, {contests} contests ({uncontested} uncontested), "
        f"{frame.height} candidates",
        file=sys.stderr,
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="English parish and town council elections, ward by ward, from the principal councils' published "
            "results: polls with votes, and wards filled unopposed as tacit sets",
            polity="GB",
        )

        def name(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        systems = {code: cs.key("gb.electoral_system", code) for code in ("fptp", "block")}
        if any(system in cs.minted for system in systems.values()):
            raise ValueError("publish gb.parliament first")
        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = %s", [WARDS]).fetchone() is None:
            cs.add(
                "scheme", code=WARDS, polity_id=gb, kind="electoral_district", rank=5, noun="parish or community ward"
            )
        sources: dict[str, UUID] = {}
        for lad, council in COUNCILS.items():
            link(council)
            if lad not in set(frame["lad"]):
                continue
            sources[lad] = cs.source(council["publisher"], council["title"])
            cs.add(
                "source",
                id=sources[lad],
                kind="official_body",
                publisher=council["publisher"],
                title=council["title"],
                url=council["url"],
                license=COUNCIL,
            )
            for url in sorted(set(frame.filter(pl.col("lad") == lad)["url"]) | set(council.get("summary", []))):
                cs.artifact(raw.cached(SOURCE, url), sources[lad])
        keys = sorted(set(frame["key"]))
        paths = dict(
            conn.execute(
                """select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
                   where e.scheme = 'gb.polity' and e.value = any(%s)""",
                [keys],
            ).fetchall()
        )
        bodies = dict(
            conn.execute(
                """select e.value, n.text from external_id e join body b on b.id = e.entity_id
                   join name n on n.entity_id = e.entity_id and n.is_preferred
                   where e.scheme = 'gb.body' and e.value = any(%s)""",
                [keys],
            ).fetchall()
        )
        if missing := sorted(set(keys) - bodies.keys()):
            raise ValueError(f"no council body for {', '.join(missing)}; emit gb.parish first")
        parties: dict[str, UUID] = {}

        def party_of(description: str) -> UUID | None:
            target = PARTIES.get(description)
            if target is None or target == "independent":
                return None
            if target not in parties:
                parties[target] = cs.key("wikidata", target)
                if parties[target] in cs.minted:
                    raise ValueError(f"{target} is not a party in the archive")
            return parties[target]

        people: dict[str, UUID] = {}
        elections: set[str] = set()
        units: set[UUID] = set()
        for (key, _, ward), lines in frame.group_by("key", "day", "ward", maintain_order=True):
            assert isinstance(key, str)
            first = lines.row(0, named=True)
            day = first["day"]
            places = first["places"]
            tacit = first["votes"] is None
            polity = cs.key("gb.polity", key)
            parish_area = cs.area(first["scheme"], first["code"])
            election_key = f"{day}:pc:{key}"
            election = cs.key("gb.election", election_key)
            election_path = f"{paths[key]}/{day}-local"
            if election_key not in elections:
                elections.add(election_key)
                cs.add(
                    "election", id=election, polity_id=polity, kind="local", date_from=day, date_to=day, status="held"
                )
                name(election, f"{day.year} {bodies[key]} election")
                cs.add("slug", path=election_path, entity_id=election)
                cs.add("note", entity_id=election, kind="context", body_md=CONTEXT)
            if ward is None:
                scheme, code, area = first["scheme"], first["code"], parish_area
            else:
                scheme, code = WARDS, f"{key}:{slug(ward)}"
                area = cs.area(WARDS, code)
                if area in cs.minted and area not in units:
                    units.add(area)
                    cs.add("area", id=area, scheme_code=WARDS)
                    cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=area, parent_id=parish_area, valid_from=None, valid_to=None)
                name(area, ward)
            unit = cs.unit(scheme, code)
            if unit in cs.minted and unit not in units:
                units.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, ward or first["parish"])
            contest_key = f"{election_key}:{slug(ward) if ward else 'council'}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=cs.key("gb.body", key),
                district_area_id=area,
                magnitude=places,
                round_number=1,
                electoral_system_id=systems["fptp" if places == 1 else "block"],
                default_result_kind="tacit" if tacit else "counted",
            )
            name(contest, f"{bodies[key]}, {ward} ward, {day.year}" if ward else f"{bodies[key]}, {day.year}")
            cs.add("slug", path=f"{election_path}/{slug(ward) if ward else 'council'}", entity_id=contest)
            source = sources[first["lad"]]
            cs.add("citation", entity_id=contest, source_id=source)
            locator: dict[str, object] = {"url": first["url"]}
            if first["page"] is not None:
                locator["page"] = first["page"]
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("citation", entity_id=result_set, source_id=source, locator=locator)
            publisher = COUNCILS[first["lad"]]["publisher"]
            if first["remark"]:
                cs.add(
                    "note", entity_id=result_set, kind="context", body_md=f"The declaration reads: “{first['remark']}”"
                )
            if tacit and places is None:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNSTATED.format(publisher=publisher))
            for field, measure in MEASURES.items():
                if first[field] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=first[field],
                    )
            for line in lines.iter_rows(named=True):
                option_key = f"{contest_key}:{line['number']}"
                person_key = f"{key}:{slug(line['name'])}"
                if person_key not in people:
                    people[person_key] = cs.key(f"{SOURCE}.person", person_key)
                    cs.add("person", id=people[person_key])
                    name(people[person_key], line["name"])
                party = party_of(line["description"])
                if line["description"] and line["description"] not in PARTIES:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=f"{line['name']} stood as “{line['description']}”; no party is linked.",
                    )
                option = cs.key(f"{SOURCE}.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=line["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", option_key),
                    person_id=people[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=line["printed"],
                    member_of_party_id=party,
                    is_independent=PARTIES.get(line["description"]) == "independent",
                )
                if line["votes"] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=0,
                        flags=0,
                        value=line["votes"],
                    )
                if line["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
