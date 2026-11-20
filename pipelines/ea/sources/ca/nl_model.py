"""The shapes the Newfoundland and Labrador report parsers share, and the report files they read."""

from dataclasses import dataclass, field
from datetime import date
from typing import Final

SOURCE: Final = "ca.nl"
SITE: Final = "https://www.elections.gov.nl.ca/files/"
OFFICIAL: Final = "https://www.elections.gov.nl.ca/resources/ereports/"
GENERALS: Final = {
    1972: SITE + "resources-pdf-electionreports-genelections-gereport1972.pdf",
    1975: SITE + "resources-pdf-electionreports-genelections-gereport1975.pdf",
    1979: SITE + "resources-pdf-electionreports-genelections-gereport1979.pdf",
    1982: SITE + "resources-pdf-electionreports-genelections-gereport1982.pdf",
    1985: SITE + "resources-pdf-electionreports-genelections-gereport1985.pdf",
    1989: SITE + "resources-pdf-electionreports-genelections-gereport1989.pdf",
    1993: SITE + "resources-pdf-electionreports-genelections-gereport1993.pdf",
    1996: SITE + "resources-pdf-electionreports-genelections-gereport1996.pdf",
    1999: SITE + "resources-pdf-electionreports-genelections-gereport1999.pdf",
    2003: SITE + "resources-pdf-electionreports-genelections-gereport2003.pdf",
    2007: SITE + "resources-pdf-electionreports-genelections-gereport2007.pdf",
    2011: SITE + "resources-pdf-electionreports-genelections-gereport2011.pdf",
    2015: SITE + "resources-pdf-electionreports-genelections-gereport2015.pdf",
    2019: SITE + "resources-pdf-electionreports-genelections-gereport2019.pdf",
    2021: SITE + "resources-pdf-electionreports-genelections-gereport2021.pdf",
    2025: SITE + "2025-General-Election-Report.pdf",
}
BY_ELECTION_FILES: Final = (
    SITE + "resources-pdf-electionreports-byelections-baieverte-greenbayby-electionreport.pdf",
    SITE + "resources-pdf-electionreports-byelections-fogoisland-capefreelsby-electionreport.pdf",
    SITE + "resources-pdf-electionreports-byelections-conceptionbayeast-bellislandby-electionreport.pdf",
    SITE + "resources-pdf-electionreports-byelections-oceohumbergrosmornebyelectionreport2020.pdf",
    SITE + "resources-pdf-electionreports-byelections-19.topsailparadise.pdf",
    SITE + "resources-pdf-electionreports-byelections-18.windsorlake.pdf",
    SITE + "resources-pdf-electionreports-byelections-17.mountpearlnorth.pdf",
    SITE + "resources-pdf-electionreports-byelections-14-cbs-by-election-report-plain.pdf",
    SITE + "resources-pdf-electionreports-byelections-14-sgse-by-election-report-plain.pdf",
    SITE + "resources-pdf-electionreports-byelections-14-vw-by-election-report-plain-final.pdf",
    SITE + "resources-pdf-electionreports-byelections-13-chg-by-election-report-plain.pdf",
    SITE + "resources-pdf-electionreports-byelections-13-clac-by-election-report-plain.pdf",
    SITE + "resources-pdf-electionreports-byelections-11-hw-by-election-report.pdf",
    SITE + "resources-pdf-electionreports-byelections-10-cbe-bi-by-election-report.pdf",
    SITE + "resources-pdf-electionreports-byelections-10.topsailbyelectionreport.pdf",
    SITE + "resources-pdf-electionreports-byelections-09-tn-by-election.pdf",
    SITE + "resources-pdf-electionreports-byelections-09-swbn-by-election.pdf",
    SITE + "resources-pdf-electionreports-byelections-08-cape-st.francis-and-baie-verte-springdale-aug-27.pdf",
    SITE + "resources-pdf-electionreports-byelections-07.labradorwest-march13.pdf",
    SITE + "resources-pdf-electionreports-byelections-07.humbervalley-february12.pdf",
    SITE + "resources-pdf-electionreports-byelections-07.portauport-february8.pdf",
    SITE + "resources-pdf-electionreports-byelections-07.kilbride-february8.pdf",
    SITE + "resources-pdf-electionreports-byelections-07.ferryland-february8.pdf",
    SITE + "resources-pdf-electionreports-byelections-05-exploits-june-23.pdf",
    SITE + "resources-pdf-electionreports-byelections-02-conception-bay-south-nov-12.pdf",
    SITE + "resources-pdf-electionreports-byelections-02-bonavista-north-july-24.pdf",
    SITE + "resources-pdf-electionreports-byelections-01-st.barbe-and-the-straights-white-bay-north-jan-30.pdf",
    SITE + "resources-pdf-electionreports-byelections-01-humber-west-port-de-grave-june-19.pdf",
    SITE + "resources-pdf-electionreports-byelections-00-trinity-north-april-25.pdf",
    SITE + "resources-pdf-electionreports-byelections-97-st.johns-west-july-21.pdf",
    SITE + "resources-pdf-electionreports-byelections-95-gander-oct-10.pdf",
    SITE + "resources-pdf-electionreports-byelections-95-grand-falls-june.27.pdf",
    SITE + "resources-pdf-electionreports-byelections-94-placentia-feb-21.pdf",
    SITE + "resources-pdf-electionreports-byelections-92-naskaupi-june-25.pdf",
    SITE + "resources-pdf-electionreports-byelections-92-ferryland-june-25.pdf",
    SITE + "resources-pdf-electionreports-byelections-90-st.johns-east-trinity-north-dec-11.pdf",
    SITE + "resources-pdf-electionreports-byelections-89-bay-of-islands-may-31-and-trinity-north-oct-3.pdf",
    SITE + "resources-pdf-electionreports-byelections-88-waterford-kenmount-mar-9.pdf",
    SITE + "resources-pdf-electionreports-byelections-87-windsor-buchans-dec-17.pdf",
    SITE + "resources-pdf-electionreports-byelections-86-st.johns-east-and-st.johns-east-extern-dec-9.pdf",
    SITE + "resources-pdf-electionreports-byelections-84-menihek-oct-9.pdf",
    SITE + "resources-pdf-electionreports-byelections-83-terra-nova-dec-7.pdf",
    SITE + "resources-pdf-electionreports-byelections-81-bellevue-apr-10.pdf",
    SITE + "resources-pdf-electionreports-byelections-79-st.marys-the-capes-oct-30-burgeo-bay-despoir-nov-29.pdf",
    SITE + "resources-pdf-electionreports-byelections-77-twillingate-dec-8.pdf",
    SITE + "resources-pdf-electionreports-byelections-77-ferryland-st.johns-west-jun-16.pdf",
    SITE + "resources-pdf-electionreports-byelections-76-bonavistanorth-exploits-ferryland-jun-30.pdf",
    SITE + "resources-pdf-electionreports-byelections-73-hermitage-nov-23.pdf",
    SITE + "resources-pdf-electionreports-byelections-72-labrador-south-aug-31.pdf",
    SITE + "resources-pdf-electionreports-byelections-waterfordvalleyby-electionreport2024.pdf",
)


@dataclass
class Candidate:
    """One candidate as the report prints the name (`SURNAME, Given`), with the party code the report prints beside
    it (`LIB`, `PC`, `N-A`, or None when the report leaves it blank) and the votes of the official summary."""

    name: str
    party: str | None
    votes: int
    elected: bool = False


@dataclass
class Poll:
    """One row of a poll-by-poll table. `votes` follows the district's candidate order."""

    code: str  # the printed poll number, or the printed label of an unnumbered row such as `Advance - La Scie`
    description: str | None
    electors: int | None
    swear_ins: int | None
    votes: list[int]
    rejected: int | None
    total: int | None


@dataclass
class District:
    number: int | None
    name: str
    candidates: list[Candidate]
    electors: int | None  # the revised list printed for the district
    counted: int | None  # total votes counted (valid ballots)
    rejected: int | None
    cast: int | None
    polls: list[Poll]
    totals: Poll | None  # the district's total row of the poll table, when printed
    acclaimed: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Election:
    day: date
    kind: str  # `general` or `by_election`
    url: str  # the report the figures come from
    districts: list[District]
    legend: dict[str, str] = field(default_factory=dict)  # party code -> party name as the report prints it
    notes: list[str] = field(default_factory=list)
