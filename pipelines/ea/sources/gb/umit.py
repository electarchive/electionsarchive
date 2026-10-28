import itertools
import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.umit"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
DATAVERSE = "https://dataverse.harvard.edu/api/access/datafile"
RESULTS = f"{DATAVERSE}/14037637?format=original"
README = f"{DATAVERSE}/10549598"
LIBRARY = (
    "https://web.archive.org/web/20201106192521if_/"
    "https://researchbriefings.files.parliament.uk/documents/CBP-8647/1918-2019election_results_by_pcon.xlsx"
)
LIBRARY_PAGE = "https://commonslibrary.parliament.uk/research-briefings/cbp-8647/"
LIBRARY_TITLE = "General election results from 1918 to 2019 (CBP-8647)"
LIBRARY_SHEETS = {"1974F": date(1974, 2, 28), "1974O": date(1974, 10, 10)}
REGISTER = "https://search.electoralcommission.org.uk/api/pdf/Registrations/PP120"
REFORM = "https://search.electoralcommission.org.uk/api/pdf/Registrations/PP7931"
REFORM_PAGE = "https://search.electoralcommission.org.uk/English/Registrations/PP7931"
REGISTER_PAGE = "https://search.electoralcommission.org.uk/English/Registrations/PP120"
PUBLISHER = "Resul Umit, Durham University"
TITLE = "UK House of Commons Election Results at Candidate Level, version 13 (2026-06-30)"
LANDING = "https://doi.org/10.7910/DVN/S83HOA"
NOTES = (
    "Candidate-level general election results for the House of Commons, 1832–2024, compiled by Resul Umit from "
    "electionhub.co.uk with corrections from the House of Commons, Wikipedia, and Eggers and Spirling (2014); 2024 "
    "comes from the House of Commons Library (Cracknell and Baker, CBP-10009). It marks no winners."
)
SKIPPED = date(1868, 11, 17)
PARTITION = date(1922, 12, 6)
DECLARED = {
    ("1841-06-29", "eng-marlborough"): ("Baring, Henry Bingham", "Brudenell-Bruce, Ernest Augustus Charles"),
    ("1847-07-29", "eng-maidstone"): ("Dodd, George", "Hope, Alexander James Beresford"),
    ("1847-07-29", "wls-montgomery-boroughs"): ("Pugh, David",),
    ("1852-07-07", "eng-knaresborough"): ("Dent, John Dent", "Woodd, Basil Thomas"),
    ("1859-04-28", "eng-east-suffolk"): ("Henniker-Major, John", "Kelly, Fitzroy Edward"),
    ("1859-04-28", "wls-flint-boroughs"): ("Hanmer, John",),
    ("1865-07-11", "eng-truro"): ("Vivian, John Cranch Walker", "Williams, Frederick Martin"),
    ("1865-07-11", "sct-dunbartonshire"): ("Smollett, Patrick Boyle",),
    ("1874-01-31", "eng-north-lincolnshire"): ("Astley, John Dugdale", "Winn, Rowland"),
    ("1880-03-31", "eng-dorset"): ("Digby, Edward Henry Trafalgar", "Floyer, John", "Portman, William Henry Berkeley"),
    ("1880-03-31", "eng-maidstone"): ("Freke-Aylmer, John Evans", "Ross, Alexander Henry"),
    ("1880-03-31", "eng-sandwich"): ("Brassey, Henry Arthur", "Knatchbull-Hugessen, Edward Hugessen"),
    ("1910-12-03", "irl-east-mayo"): ("Dillon, John",),
    ("1910-12-03", "irl-north-mayo"): ("Boyle, Daniel",),
    ("1910-12-03", "irl-south-mayo"): ("Fitzgibbon, John",),
    ("1918-12-14", "eng-cambridge-university"): ("Larmor, Joseph", "Rawlinson, John Frederick Peel"),
    ("1918-12-14", "eng-combined-english-universities"): ("Conway, William Martin", "Fisher, Herbert Albert Laurens"),
    ("1918-12-14", "eng-oxford-university"): ("Gascoyne-Cecil, Hugh Richard Heathcote", "Prothero, Rowland Edmund"),
    ("1918-12-14", "irl-dublin-university"): ("Samuels, Arthur Warren", "Woods, Robert Henry"),
    ("1918-12-14", "sct-combined-scottish-universities"): (
        "Cheyne, William Watson",
        "Cowan, Dugald McCoig",
        "Craik, Henry",
    ),
    ("1922-11-15", "eng-cambridge-university"): ("Butler, James Ramsay Montagu", "Rawlinson, John Frederick Peel"),
    ("1922-11-15", "eng-combined-english-universities"): ("Conway, William Martin", "Fisher, Herbert Albert Laurens"),
    ("1922-11-15", "eng-oxford-university"): (
        "Cecil, Hugh Richard Heathcote Gascoyne",
        "Oman, Charles William Chadwick",
    ),
    ("1923-12-06", "eng-cambridge-university"): ("Butler, George Geoffrey Gilbert", "Rawlinson, John Frederick Peel"),
    ("1923-12-06", "eng-combined-english-universities"): ("Conway, William Martin", "Fisher, Herbert Albert Laurens"),
    ("1923-12-06", "eng-oxford-university"): (
        "Cecil, Hugh Richard Heathcote Gascoyne",
        "Oman, Charles William Chadwick",
    ),
    ("1924-10-29", "eng-cambridge-university"): ("Butler, George Geoffrey Gilbert", "Rawlinson, John Frederick Peel"),
    ("1924-10-29", "eng-combined-english-universities"): ("Conway, William Martin", "Fisher, Herbert Albert Laurens"),
    ("1924-10-29", "eng-oxford-university"): (
        "Cecil, Hugh Richard Heathcote Gascoyne",
        "Oman, Charles William Chadwick",
    ),
    ("1924-10-29", "sct-combined-scottish-universities"): (
        "Berry, George Andreas",
        "Cowan, Dugald McCoig",
        "Craik, Henry",
    ),
    ("1929-05-30", "eng-cambridge-university"): ("Wilson, Godfrey Harold Alfred", "Withers, John James"),
    ("1929-05-30", "eng-oxford-university"): (
        "Gascoyne-Cecil, Hugh Richard Heathcote",
        "Oman, Charles William Chadwick",
    ),
    ("1929-05-30", "sct-combined-scottish-universities"): (
        "Berry, George Andreas",
        "Buchan, John",
        "Cowan, Dugald McCoig",
    ),
    ("1929-05-30", "eng-combined-english-universities"): ("Conway, William Martin", "Rathbone, Eleanor Florence"),
    ("1931-10-27", "eng-combined-english-universities"): ("Craddock, Reginald Henry", "Rathbone, Eleanor Florence"),
    ("1935-11-14", "eng-cambridge-university"): ("Pickthorn, Kenneth William Murray", "Withers, John James"),
    ("1935-11-14", "eng-oxford-university"): ("Gascoyne-Cecil, Hugh Richard Heathcote", "Herbert, Alan Patrick"),
    ("1935-11-14", "sct-combined-scottish-universities"): (
        "Kerr, John Graham",
        "Morrison, George Alexander",
        "Skelton, Archibald Noel",
    ),
    ("1945-07-05", "eng-cambridge-university"): ("Harris, Henry Wilson", "Pickthorn, Kenneth William Murray"),
    ("1945-07-05", "eng-combined-english-universities"): ("Lindsay, Kenneth Martin", "Rathbone, Eleanor Florence"),
    ("1945-07-05", "eng-oxford-university"): ("Herbert, Alan Patrick", "Salter, James Arthur"),
    ("1945-07-05", "sct-combined-scottish-universities"): ("Anderson, John", "Kerr, John Graham", "Orr, John Boyd"),
    ("1857-03-27", "eng-huntingdonshire"): ("Rust, James", "Fellowes, Edward"),
    ("1859-04-28", "eng-west-suffolk"): ("Hervey, Frederick William John", "Parker, William"),
    ("1841-06-29", "eng-thetford"): ("Baring, Bingham", "Flower, James"),
    ("1865-07-11", "eng-north-durham"): ("Williamson, Hedworth", "Shafto, Robert Duncombe"),
    ("1874-01-31", "eng-south-lincolnshire"): ("Turnor, Edmund", "Welby, William Earle"),
}
HANSARD = {
    ("1857-03-27", "eng-huntingdonshire"),
    ("1859-04-28", "eng-west-suffolk"),
    ("1841-06-29", "eng-thetford"),
    ("1865-07-11", "eng-north-durham"),
    ("1874-01-31", "eng-south-lincolnshire"),
}
NAMESAKES = (
    ("eng-tamworth", "Peel, Robert", 1832, 1847, "@1788"),
    ("sct-kirkcaldy-boroughs", "Ferguson, Robert", 1841, 1868, "@1802"),
    ("wls-cardigan-boroughs", "Pryse, Pryse", 1849, 1855, "@1815"),
    ("irl-kerry", "Herbert, Henry Arthur", 1866, 1880, "@1840"),
    ("eng-richmond-yorks", "Dundas, John Charles", 1873, 1885, "@1845"),
    ("eng-york", "Dundas, John Charles", 1886, 1886, "@1845"),
    ("eng-berkshire", "Walter, John", 1852, 1880, "@1818"),
    ("eng-cambridgeshire", "Yorke, Charles Philip", 1865, 1865, "@1836"),
    ("eng-clitheroe", "Fort, Richard", 1880, 1880, "@1856"),
    ("eng-clitheroe", "Fort, Richard", 1945, 1955, "@1907"),
    ("eng-dorchester", "Williams, Robert", 1835, 1837, "@1811"),
    ("eng-great-grimsby", "Heneage, Edward", 1880, 1895, "@1840"),
    ("eng-hertford", "Smith, Abel", 1900, 1906, "@1862"),
    ("eng-hertfordshire", "Smith, Abel", 1859, 1880, "@1829"),
    ("eng-horsham", "Hurst, Robert Henry", 1865, 1874, "@1817"),
    ("eng-leeds", "Baines, Edward", 1859, 1874, "@1800"),
    ("eng-lewes", "Beamish, Tufton Victor Hamilton", 1945, 1959, "@1917"),
    ("eng-lymington", "Mackinnon, William Alexander", 1857, 1865, "@1813"),
    ("eng-rye", "Mackinnon, William Alexander", 1852, 1852, "@1813"),
    ("eng-newcastle-upon-tyne", "Cowen, Joseph", 1874, 1885, "@1829"),
    ("eng-north-shropshire", "Hill, Rowland", 1857, 1857, "@1833"),
    ("eng-south-dorset", "Gascoyne-Cecil, Robert Arthur James", 1929, 1935, "@1893"),
    ("eng-south-molton", "Lambert, George", 1945, 1945, "@1909"),
    ("eng-sunderland", "Storey, Samuel", 1931, 1945, "@1896"),
    ("eng-the-hartlepools", "Richardson, Thomas", 1892, 1900, "@1846"),
    ("eng-west-derbyshire", "White, Charles Frederick", 1945, 1945, "@1891"),
    ("eng-westmorland", "Lowther, William", 1874, 1880, "@1821"),
    ("eng-westmorland", "Taylour, Thomas", 1874, 1880, "@1844"),
    ("sct-dumfriesshire", "Hope-Johnstone, John James", 1874, 1874, "@1841"),
    ("sct-renfrewshire", "Mure, William", 1874, 1880, "@1830"),
    ("wls-brecon", "Morgan, Charles Rodney", 1852, 1852, "@1828"),
    ("wls-carmarthenshire", "Jones, John", 1874, 1880, "@1812"),
    ("wls-montgomery", "Williams-Wynn, Charles Watkin", 1865, 1885, "@1822"),
    ("wls-radnorshire", "Walsh, Arthur Henry John", 1885, 1886, "@1859"),
)
DUPLICATES = ((1955, "eng-finchley", "Sykes, A. M. P. H."),)
PRINTED = {
    "ec2005": (
        "Electoral Commission",
        "Election 2005: constituencies, candidates and results",
        (
            "https://web.archive.org/web/20110615071530id_/http://www.electoralcommission.org.uk/__data/assets/pdf_file/0007/47185/"
            "Election2005_constituencies,candidatesandresultsFINAL_20719-15241__E__N__S__W__.pdf"
        ),
    ),
}
RECOUNTED = {
    (date(2005, 5, 5), "eng-east-kingston-upon-hull", "Lindsay, Katy"): (4138, 4038, "ec2005"),
    (date(2005, 5, 5), "nir-upper-bann", "Trimble, David"): (11381, 11281, "ec2005"),
    (date(2005, 5, 5), "eng-workington", "Pattinson, Judith"): (11659, 12659, "ec2005"),
}
WORKING_NOTE = re.compile(
    r"^\s*[\d,]+\s*$|^Succession of \w+$|\bnot in (?:the )?book\b|\bnot in (?:EH|FWSC)\b", re.IGNORECASE
)
ALLIANCE = (
    ("1983-06-09", "eng-truro", "Penhaligon, David", "Liberal"),
    ("1987-06-11", "eng-truro", "Taylor, Matthew", "Liberal"),
)
TWO_MEMBER = {"eng-sheffield"}
SEATS = (
    ("eng-bodmin", 1832, 1865, 2),
    ("eng-cirencester", 1832, 1865, 2),
    ("eng-hertford", 1832, 1865, 2),
    ("eng-huntingdon", 1832, 1865, 2),
    ("eng-windsor", 1865, 1865, 2),
    ("eng-wycombe", 1865, 1865, 2),
    ("eng-durham", 1832, 1880, 2),
    ("eng-rutland", 1832, 1880, 2),
    ("eng-bury", 1880, 1880, 1),
    ("eng-liverpool", 1859, 1865, 2),
    ("eng-salford", 1865, 1865, 1),
    ("nir-belfast-east", 1922, 1945, 1),
    ("eng-blackburn", 1918, 1945, 2),
    ("eng-bolton", 1918, 1945, 2),
    ("sct-dundee", 1918, 1945, 2),
)
VOTES, REGISTERED, VALID, INVALID = 1, 2, 4, 5
COUNTRIES = {
    "England": "ENG",
    "Wales": "WLS",
    "Scotland": "SCT",
    "Ireland": "IRL",
    "Northern Ireland": "NIR",
}
NOT_PARTIES = ("Independent", "No label")
SHAPED = date(2010, 5, 6)
REDISTRIBUTIONS = (1832, 1868, 1885, 1918, 1950, 1955, 1974, 1983, 1997)
NON_PARTY = """select p.id, n.text from party p join changeset c on c.id = p.changeset_id and c.pipeline = %s
    join name n on n.entity_id = p.id and n.is_preferred
    join entity e on e.id = p.id and e.retired_by is null
    where p.kind = 'party'
      and (n.text ~ '^Independent ' and n.text not in ('Independent Labour Party', 'Independent Irish')
           or n.text = 'Anti H-Block')
      and exists (select from contest_option o where o.party_id = p.id)
      and not exists (select from contest_option o join contest k on k.id = o.contest_id
                      join election x on x.id = k.election_id where o.party_id = p.id and x.date_from >= '2001-02-16')
    order by n.text"""
MISSPELLED = {"RoxBoroughshire": "Roxburghshire", "East Suex": "East Sussex"}
SPLIT = {(date(1880, 3, 31), "eng-east-sussex"): ("eng-east-suex", "East Suex")}
RENAMED = {
    "Central Newcastle upon Tyne": "Newcastle upon Tyne Central",
    "Central Stoke-on-Trent": "Stoke-on-Trent Central",
    "East Kingston upon Hull": "Kingston upon Hull East",
    "East Newcastle upon Tyne and Wallsend": "Newcastle upon Tyne East and Wallsend",
    "North East Wolverhampton": "Wolverhampton North East",
    "North Kingston upon Hull": "Kingston upon Hull North",
    "North Newcastle upon Tyne": "Newcastle upon Tyne North",
    "North Stoke-on-Trent": "Stoke-on-Trent North",
    "Regent's Park and Kensington North": "Regent's Park and North Kensington",
    "South East Wolverhampton": "Wolverhampton South East",
    "South Middlesbrough and East Cleveland": "Middlesbrough South and East Cleveland",
    "South Stoke-on-Trent": "Stoke-on-Trent South",
    "South West Milton Keynes": "Milton Keynes South West",
    "South West Wolverhampton": "Wolverhampton South West",
    "West Kingston upon Hull and Hessle": "Kingston upon Hull West and Hessle",
}
EXISTING = {
    "conservative": ("wikidata", "Q9626"),
    "labour": ("wikidata", "Q9630"),
    "liberal": ("wikidata", "Q622441"),
    "liberal-national": ("wikidata", "Q1354368"),
    "national-liberal-1922": ("wikidata", "Q6974098"),
    "national-labour": ("wikidata", "Q2184291"),
    "independent-conservative": ("gb.parliament.party", "11"),
    "independent-liberal": ("gb.parliament.party", "13"),
    "liberal-conservative": ("gb.parliament.party", "19"),
    "liberal-labour": ("gb.parliament.party", "20"),
    "independent-nationalist": ("gb.parliament.party", "21"),
}
ALIASES = {
    "Conservative": "conservative",
    "Tory": "conservative",
    "Unionist with Coalition Coupon": "conservative",
    "Speaker with Coalition Coupon": "speaker",
    "Conservative and National Liberal": "conservative",
    "National Liberal and Conservative": "conservative",
    "NI Conservatives": "conservative",
    "Scottish Unionist": "scottish-unionist",
    "Scottish Unionist with Coalition Coupon": "scottish-unionist",
    "Labour": "labour",
    "Labour Co-op": "labour",
    "Labour and Co-operative": "labour",
    "Labour Representation Committee": "labour",
    "Liberal with Coalition Coupon": "liberal",
    "Liberal (Local)": "liberal",
    "Liberal National": "liberal-national",
    "National Labour": "national-labour",
    "Independent Conservative": "independent-conservative",
    "Independent Liberal": "independent-liberal",
    "Independent Nationalist": "independent-nationalist",
    "Liberal-Conservative": "liberal-conservative",
    "Liberal-Labour": "liberal-labour",
    "Liberal Democrat": "liberal-democrats",
    "Liberal Democrats": "liberal-democrats",
    "Ecology": "green-party",
    "Greens": "green-party",
    "Green Party": "green-party",
    "Green": "green-party",
    "Scottish Green Party": "scottish-greens",
    "Scottish Greens": "scottish-greens",
    "SNP": "scottish-national-party",
    "Scottish National Party": "scottish-national-party",
    "UKIP": "uk-independence-party",
    "UK Independence Party": "uk-independence-party",
    "Reform UK": "reform-uk",
    "The Brexit Party": "reform-uk",
    "BNP": "british-national-party",
    "British National Party": "british-national-party",
    "DUP": "democratic-unionist-party",
    "Democratic Unionist Party": "democratic-unionist-party",
    "SDLP": "social-democratic-and-labour-party",
    "Social Democratic & Labour Party": "social-democratic-and-labour-party",
    "Social Democratic and Labour Party": "social-democratic-and-labour-party",
    "Sinn Féin": "sinn-fein",
    "Sinn Fein": "sinn-fein",
    "UUP": "ulster-unionist-party",
    "Ulster Unionist Party": "ulster-unionist-party",
    "Alliance": "alliance-party-of-northern-ireland",
    "Monster Raving Loony": "official-monster-raving-loony-party",
    "Monster Raving Loony Party": "official-monster-raving-loony-party",
    "Official Monster Raving Loony Party": "official-monster-raving-loony-party",
    "Christian Peoples": "christian-peoples-alliance",
    "Christian Peoples Alliance": "christian-peoples-alliance",
    "Christian Peoples Alliance Party": "christian-peoples-alliance",
    "Socialist Labour Party": "socialist-labour-party",
    "Workers Revolutionary": "workers-revolutionary-party",
    "Workers Revolutionary Party": "workers-revolutionary-party",
    "Mebyon Kernow - The Party for Cornwall": "mebyon-kernow",
    "Respect": "respect-party",
    "Respect Party": "respect-party",
    "Alliance For Green Socialism": "alliance-for-green-socialism",
    "Alliance for Green Socialism": "alliance-for-green-socialism",
    "Communist League Election Campaign": "communist-league",
    "Scottish Socialist": "scottish-socialist-party",
    "Scottish Socialist Party": "scottish-socialist-party",
    "Workers' Party": "workers-party",
    "The Workers' Party": "workers-party",
    "Communist Party of Britain": "communist-party-of-britain",
    "Social Democratic Party": "social-democratic-party-1990",
}
UCUNF_DAY = date(2010, 5, 6)
UCUNF = {
    ("nir-belfast-east", "Ringland, Trevor"),
    ("nir-belfast-north", "Cobain, Fred"),
    ("nir-belfast-south", "Bradshaw, Paula"),
    ("nir-belfast-west", "Manwaring, Bill"),
    ("nir-east-antrim", "McCune, Rodney"),
    ("nir-east-londonderry", "Macaulay, Lesley"),
    ("nir-foyle", "Harding, David"),
    ("nir-lagan-valley", "Trimble, Daphne"),
    ("nir-mid-ulster", "Overend, Sandra"),
    ("nir-newry-and-armagh", "Kennedy, Danny"),
    ("nir-north-antrim", "Armstrong, Irwin"),
    ("nir-north-down", "Parsley, Ian"),
    ("nir-south-antrim", "Empey, Reg"),
    ("nir-south-down", "McCallister, John"),
    ("nir-strangford", "Nesbitt, Mike"),
    ("nir-upper-bann", "Hamilton, Harry"),
    ("nir-west-tyrone", "Hussey, Ross"),
}
UCUNF_NAME = "Ulster Conservatives and Unionists – New Force"
UCUNF_NOTE = (
    "Umit's dataset labels this candidate Independent; the candidate stood for Ulster Conservatives and Unionists – "
    "New Force, the joint ticket of the Conservatives and the Ulster Unionists, which the Commons Library's "
    "constituency results (CBP-8647) count with the Ulster Unionists."
)
ARTICLES_TITLE = "UK Parliament constituency articles"
ARTICLES_NOTE = (
    "Umit's dataset labels this candidate {label}; the party shown is the one the constituency's Wikipedia article "
    "gives{basis}."
)
ARTICLES_BASIS = (
    ", citing Craig's British Parliamentary Election Results and Walker's Parliamentary Election Results in Ireland"
)
EARLY_LABELS = {"Liberal": ("Whig", 1859), "Repeal Association and Whig": ("Repeal Association", 1885)}
ARTICLE_LABELS = {
    (date(1835, 1, 6), "irl-county-louth", "Bellew, Richard Montesquieu"): "Whig",
    (date(1837, 7, 24), "irl-athlone", "O'Connell, John"): "Radicals",
    (date(1852, 7, 7), "irl-cavan", "Ellis, Hercules"): "Tenant Right League",
    (date(1852, 7, 7), "irl-londonderry", "Greer, Samuel MacCurdy"): "Radicals",
    (date(1852, 7, 7), "irl-tralee", "O'Connell, Maurice"): "Radicals",
    (date(1857, 3, 27), "irl-londonderry", "Greer, Samuel MacCurdy"): "Radicals",
    (date(1983, 6, 9), "eng-barnsley-west-and-penistone", "Evans, J"): "Social Democratic",
    (date(1983, 6, 9), "eng-bournemouth-east", "Millward, John"): "Liberal",
    (date(1983, 6, 9), "eng-canterbury", "Purchese, J"): "Liberal",
    (date(1983, 6, 9), "eng-christchurch", "Alexander, S"): "Social Democratic",
    (date(1983, 6, 9), "eng-coventry-south-east", "Kilby, G"): "Liberal",
    (date(1983, 6, 9), "eng-croydon-south", "Forrest, J"): "Liberal",
    (date(1983, 6, 9), "eng-dagenham", "Horne, J"): "Social Democratic",
    (date(1983, 6, 9), "eng-hackney-north-and-stoke-newington", "Ash, D"): "Liberal",
    (date(1983, 6, 9), "eng-loughborough", "Frears, JR"): "Social Democratic",
    (date(1983, 6, 9), "eng-north-hertfordshire", "Binney, G"): "Liberal",
    (date(1983, 6, 9), "eng-orpington", "Cook, JW"): "Liberal",
    (date(1983, 6, 9), "eng-ravensbourne", "Boston, CM"): "Liberal",
    (date(1983, 6, 9), "eng-reading-east", "Huhne, Chris"): "Social Democratic",
    (date(1983, 6, 9), "eng-richmond-and-barnes", "Watson, Alan"): "Liberal",
    (date(1983, 6, 9), "eng-sevenoaks", "Jakobi, Stephen"): "Liberal",
    (date(1983, 6, 9), "eng-sheffield-central", "Major, Patricia"): "Social Democratic",
    (date(1983, 6, 9), "eng-swindon", "Scott, DJ"): "Social Democratic",
    (date(1983, 6, 9), "eng-teignbridge", "Alderson, J"): "Liberal",
    (date(1983, 6, 9), "eng-thurrock", "Benson, D"): "Social Democratic",
    (date(1983, 6, 9), "eng-woking", "Goldenberg, Philip"): "Liberal",
    (date(1983, 6, 9), "wls-carmarthen", "Colin, J"): "Liberal",
    (date(1987, 6, 11), "eng-bournemouth-east", "Millward, John"): "Liberal",
    (date(1987, 6, 11), "eng-canterbury", "Purchese, John"): "Liberal",
    (date(1987, 6, 11), "eng-christchurch", "McKenzie, HJ"): "Social Democratic",
    (date(1987, 6, 11), "eng-croydon-south", "Morrison, I"): "Liberal",
    (date(1987, 6, 11), "eng-dagenham", "Carter, John"): "Social Democratic",
    (date(1987, 6, 11), "eng-gillingham", "Andrews, Leighton"): "Liberal",
    (date(1987, 6, 11), "eng-hackney-north-and-stoke-newington", "Taylor, Simon"): "Liberal",
    (date(1987, 6, 11), "eng-loughborough", "Fox, Roger"): "Social Democratic",
    (date(1987, 6, 11), "eng-north-hertfordshire", "Binney, G"): "Liberal",
    (date(1987, 6, 11), "eng-orpington", "Fryer, Jonathan"): "Liberal",
    (date(1987, 6, 11), "eng-ravensbourne", "Campbell, G"): "Liberal",
    (date(1987, 6, 11), "eng-reading-east", "Baring, Susan"): "Social Democratic",
    (date(1987, 6, 11), "eng-richmond-and-barnes", "Watson, Alan"): "Liberal",
    (date(1987, 6, 11), "eng-sevenoaks", "Jakobi, Stephen"): "Liberal",
    (date(1987, 6, 11), "eng-sheffield-central", "Hornby, Fiona"): "Social Democratic",
    (date(1987, 6, 11), "eng-swindon", "Scott, DJ"): "Social Democratic",
    (date(1987, 6, 11), "eng-teignbridge", "Ryder, RD"): "Liberal",
    (date(1987, 6, 11), "eng-woking", "Goldenberg, Philip"): "Liberal",
    (date(1987, 6, 11), "wls-carmarthen", "Jones, Gwynoro"): "Social Democratic",
}
ALLIANCE_DAYS = {date(1983, 6, 9), date(1987, 6, 11)}
ALLIANCE_NAME = "SDP–Liberal Alliance"
ALLIANCE_TITLE = "SDP–Liberal Alliance"
ALLIANCE_URL = "https://en.wikipedia.org/wiki/SDP%E2%80%93Liberal_Alliance"
ALLIANCE_LABELS = {"Liberal", "Social Democratic", "SDP-Liberal Alliance"}
CRAIG_TOTALS = {
    date(
        1832, 12, 8
    ): "the Whigs 441 seats and 554,719 votes, the Tories 175 and 241,284, and the Repeal Association 42 and 31,773",
    date(1852, 7, 7): "the Conservatives 330 seats and 311,481 votes and the Whigs 324 and 430,882",
    date(
        1857, 3, 27
    ): "the Whigs 377 seats and 464,127 votes, the Conservatives 264 and 239,712, and the Independent Irish "
    "13 and 12,099",
    date(1865, 7, 11): "the Liberals 369 seats and 508,821 votes and the Conservatives 289 and 346,035, 854,856 in all",
}
CRAIG_NOTE = (
    "Craig's British Parliamentary Election Results 1832–1885, as Wikipedia's article on this election cites it, gives "
    "{totals}. The constituency results here agree with the Craig figures each constituency's Wikipedia article "
    "cites; the national figures differ because Craig's national table groups and counts candidates its own way, so "
    "the totals here are not forced to match it."
)
ELECTIONS_TITLE = "UK general election articles, 1832–1880"
SAME_PERSON = {"eng-liverpool:temple-henry-john": ("wikidata", "Q193656")}
COMPACT_DAY = date(1837, 7, 24)
COMPACT_NAME = "Lichfield House compact"
COMPACT_TITLE = "Lichfield House Compact"
COMPACT_URL = "https://en.wikipedia.org/wiki/Lichfield_House_Compact"
RELABELED = {
    (date(1950, 2, 23), "eng-widnes", "MacColl, James Eugene"): (
        "Labour",
        "Umit's dataset labels this candidate Conservative; the Commons Library's constituency results (CBP-8647) list him as Labour, the party he sat for.",
        True,
    ),
    (date(1964, 10, 15), "eng-tavistock", "Elswood, John A"): (
        "Labour",
        "Umit's dataset labels this candidate Liberal; the Commons Library's constituency results (CBP-8647) list him as Labour.",
        True,
    ),
    (date(1950, 2, 23), "eng-halifax", "Blackburn, RH"): (
        None,
        "Umit's dataset labels this candidate National Liberal and Conservative; the Commons Library's constituency results (CBP-8647) count him among other candidates, not with either party.",
        True,
    ),
    (date(1979, 5, 3), "eng-blyth", "Nicholson, Emma"): (
        "Conservative",
        "Umit's dataset labels this candidate Communist; the Commons Library's constituency results (CBP-8647) list her as Conservative.",
        True,
    ),
    (date(1923, 12, 6), "eng-salisbury", "Morrison, Hugh"): (
        "Conservative",
        "Umit's dataset labels this candidate Liberal; the Commons Library's constituency results (CBP-8647) list him as Conservative, the party he had sat for since 1918.",
        True,
    ),
    (date(1922, 11, 15), "eng-central-newcastle-upon-tyne", "Dodd, John"): (
        "Liberal",
        "Umit's dataset prints this candidate's label as \"L+F24305iberal\", a spreadsheet slip; the Commons Library's constituency results (CBP-8647) list him as Liberal.",
        True,
    ),
    (date(1950, 2, 23), "eng-hexham", "Brown, Douglas Clifton"): (
        "Speaker",
        "Umit's dataset labels this candidate Conservative; he was Speaker at the dissolution and stood for re-election as Speaker.",
        False,
    ),
    (date(1955, 5, 26), "eng-cirencester-and-tewkesbury", "Morrison, William Shepherd"): (
        "Speaker",
        "Umit's dataset labels this candidate Conservative; he was Speaker at the dissolution and stood for re-election as Speaker, and the Commons Library's constituency results (CBP-8647) do not count him as a Conservative.",
        True,
    ),
    (date(1964, 10, 15), "eng-cities-of-london-and-westminster", "Hylton-Foster, Harry Braustyn Hylton"): (
        "Speaker",
        "Umit's dataset labels this candidate Conservative; he was Speaker at the dissolution and stood for re-election as Speaker.",
        False,
    ),
    (date(1966, 3, 31), "eng-southampton-itchen", "King, Horace Maybray"): (
        "Speaker",
        "Umit's dataset labels this candidate Labour; he was Speaker at the dissolution and stood for re-election as Speaker, and the Commons Library's constituency results (CBP-8647) do not count him as Labour.",
        True,
    ),
    (date(1970, 6, 18), "eng-southampton-itchen", "King, Horace Maybray"): (
        "Speaker",
        "Umit's dataset labels this candidate Labour; he was Speaker at the dissolution and stood for re-election as Speaker.",
        False,
    ),
    (date(1974, 2, 28), "eng-wirral", "Lloyd, John"): (
        "Speaker",
        "Umit's dataset labels this candidate Conservative; he was Speaker at the dissolution and stood for re-election as Speaker, and the Commons Library's constituency results (CBP-8647) do not count him as a Conservative.",
        True,
    ),
    (date(1974, 10, 10), "eng-wirral", "Lloyd, John"): (
        "Speaker",
        "Umit's dataset labels this candidate Conservative; he was Speaker at the dissolution and stood for re-election as Speaker.",
        False,
    ),
}
ERAS = {
    ("Liberal", 1989): "liberal-party-1989",
    ("Social Democratic", 1981): "social-democratic-party-1981",
    ("Social Democratic", 1992): "social-democratic-party-1990",
    ("Communist", 1922): "communist-party-of-great-britain",
    ("Communist", 1992): "communist-party-of-britain",
    ("National Liberal", 1922): "national-liberal-1922",
    ("National Liberal", 1931): "liberal-national",
    ("Socialist Labour", 1918): "socialist-labour-party-1903",
    ("Socialist Labour", 1992): "socialist-labour-party",
    ("Reform UK", 1997): "reform-uk-2001",
    ("Reform UK", 2018): "reform-uk",
    ("Scottish Unionist", 1986): "scottish-unionist-party",
    ("Irish Nationalist", 1885): "irish-parliamentary",
    ("Irish Nationalist", 1891): "irish-national-federation",
    ("Irish Nationalist", 1900): "irish-parliamentary",
    ("Irish Nationalist", 1919): "irish-nationalist",
    ("Whig", 1974): "whig-1974",
}
NAMES = {
    "scottish-unionist": "Unionist Party (Scotland)",
    "liberal-democrats": "Liberal Democrats",
    "green-party": "Green Party",
    "scottish-greens": "Scottish Green Party",
    "scottish-national-party": "Scottish National Party",
    "uk-independence-party": "UK Independence Party",
    "reform-uk": "Reform UK",
    "british-national-party": "British National Party",
    "democratic-unionist-party": "Democratic Unionist Party",
    "social-democratic-and-labour-party": "Social Democratic and Labour Party",
    "sinn-fein": "Sinn Féin",
    "ulster-unionist-party": "Ulster Unionist Party",
    "alliance-party-of-northern-ireland": "Alliance Party of Northern Ireland",
    "official-monster-raving-loony-party": "Official Monster Raving Loony Party",
    "christian-peoples-alliance": "Christian Peoples Alliance",
    "socialist-labour-party": "Socialist Labour Party",
    "socialist-labour-party-1903": "Socialist Labour Party",
    "workers-revolutionary-party": "Workers Revolutionary Party",
    "mebyon-kernow": "Mebyon Kernow",
    "respect-party": "Respect Party",
    "alliance-for-green-socialism": "Alliance for Green Socialism",
    "communist-league": "Communist League",
    "scottish-socialist-party": "Scottish Socialist Party",
    "workers-party": "Workers' Party",
    "communist-party-of-britain": "Communist Party of Britain",
    "communist-party-of-great-britain": "Communist Party of Great Britain",
    "social-democratic-party-1981": "Social Democratic Party",
    "social-democratic-party-1990": "Social Democratic Party",
    "liberal-party-1989": "Liberal Party",
}
DERIVED_CAVEAT = (
    "Our source for this result doesn't record who won, and we have no declared result for it. The candidates shown"
    " as elected are the ones with the most votes, one for each seat."
)
STV_CAVEAT = (
    "University seats elected their members by the single transferable vote from 1918 to 1945. Our source gives "
    "first preferences only and doesn't record who won, so no candidate is shown as elected."
)
MISSING_CAVEAT = (
    "Our source lists no more candidates than seats in this contested race, so some candidates are missing and no "
    "candidate is shown as elected."
)
TIE_CAVEAT = (
    "Two or more candidates tie for the last seat in our source, so the tied candidates are not shown as elected."
)
DECLARED_CAVEAT = (
    "Our source for this result doesn't record who won. The candidates shown as elected are the members Wikidata "
    "records as returned for this seat at this election."
)
HANSARD_CAVEAT = (
    "Our source for this result doesn't record who won at least one seat here. The candidates shown as elected are "
    "the members Parliament's Historic Hansard records as sitting for this seat from this election."
)
COUNTED = {("1929-05-30", "eng-combined-english-universities")}
COUNTED_CAVEAT = (
    "Our source for this result doesn't record who won. The candidates shown as elected are the ones the House of "
    "Commons Library's general election results (CBP-8647) give as elected at the second stage of the count."
)
UNOPPOSED_CAVEAT = (
    "Our source marks every candidate here as unopposed but lists more candidates than seats, so no candidate is "
    "shown as elected."
)
STV = "Single transferable vote (university seats, 1918–1945)"


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def fetch() -> None:
    raw.fetch(SOURCE, RESULTS, suffix=".csv")
    raw.fetch(SOURCE, README, suffix=".pdf")
    raw.fetch(SOURCE, LIBRARY, suffix=".xlsx")
    raw.fetch(SOURCE, REGISTER, suffix=".pdf")
    raw.fetch(SOURCE, REFORM, suffix=".pdf")
    for _, _, url in PRINTED.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def library() -> pl.DataFrame:
    book = fastexcel.read_excel(raw.cached(SOURCE, LIBRARY).path)
    rows: list[tuple[str, int, str, int]] = []
    for sheet in book.sheet_names[1:]:
        cells = book.load_sheet(sheet, header_row=None).to_polars().rows()
        head = next(
            i for i, row in enumerate(cells) if any(str(cell).strip() == "Constituency" for cell in row if cell)
        )
        name = next(j for j, cell in enumerate(cells[head]) if cell and str(cell).strip() == "Constituency")
        count = next(j for j, cell in enumerate(cells[head]) if cell and str(cell).strip().startswith("Electorate"))
        for row in cells[head + 1 :]:
            if row[name] and row[count] not in (None, "") and re.fullmatch(r"[0-9.]+", str(row[count])):
                rows.append((sheet, int(sheet[:4]), str(row[name]), int(float(row[count]))))
    return pl.DataFrame(rows, schema=["sheet", "year", "name", "electorate"], orient="row")


def words(text: str) -> frozenset[str]:
    plain = fold(text.replace("'", "")).replace("boroughs", "burghs").replace("borough", "burgh")
    return frozenset(plain.split()) - {"and", "the", "of", "division", "district"}


def parse() -> None:
    frame = (
        pl.read_csv(raw.cached(SOURCE, RESULTS).path, infer_schema_length=0)
        .with_columns(
            pl.col("date_start", "date_end").str.to_date(),
            pl.col("year", "seats").cast(pl.Int32),
            pl.col("votes", "electorate", "votes_valid", "votes_invalid").cast(pl.Int64),
            country=pl.col("country").replace_strict(COUNTRIES),
            unopposed=pl.col("unopposed") == "1",
        )
        .filter(pl.col("date_start") != SKIPPED)
        .filter(pl.col("date_start") == pl.col("date_start").mode().first().over("election"))
    )
    frame = frame.with_columns(
        unit=pl.col("country").str.to_lowercase() + "-" + pl.col("constituency").map_elements(slug, pl.String)
    )
    for (day, unit), (into, label) in SPLIT.items():
        chosen = (pl.col("date_start") == day) & (pl.col("unit") == unit)
        if frame.filter(chosen).height == 0:
            raise ValueError(f"no {unit} rows on {day}")
        frame = frame.with_columns(
            unit=pl.when(chosen).then(pl.lit(into)).otherwise("unit"),
            constituency=pl.when(chosen).then(pl.lit(label)).otherwise("constituency"),
        )
    usual = frame.group_by("unit").agg(usual=pl.col("seats").drop_nulls().mode().max())
    usual = usual.with_columns(usual=pl.when(pl.col("unit").is_in(TWO_MEMBER)).then(2).otherwise("usual"))
    frame = (
        frame.join(usual, on="unit")
        .with_columns(seats=pl.col("seats").max().over("date_start", "unit").fill_null(pl.col("usual")))
        .drop("usual")
    )
    frame = frame.with_columns(correction=pl.lit(None, pl.String))
    for unit, first, last, seats in SEATS:
        chosen = (pl.col("unit") == unit) & pl.col("year").is_between(first, last)
        frame = frame.with_columns(
            correction=pl.when(chosen & (pl.col("seats") != seats))
            .then(
                pl.format(
                    f"Umit's dataset gives a seat count of {{}} here; the constituency returned {'one member' if seats == 1 else 'two members'}.",
                    pl.col("seats"),
                )
            )
            .otherwise("correction"),
            seats=pl.when(chosen).then(seats).otherwise("seats"),
        )
    frame = frame.with_columns(printed=pl.lit(None, pl.String))
    for (day, unit, candidate), (votes, other, tag) in RECOUNTED.items():
        chosen = (pl.col("date_start") == day) & (pl.col("unit") == unit) & (pl.col("candidate") == candidate)
        found = frame.filter(chosen)["votes"].to_list()
        if found not in ([votes], [other]):
            raise ValueError(f"{candidate} in {unit} on {day} has {found} votes, neither {votes:,} nor {other:,}")
        publisher, title, _ = PRINTED[tag]
        given = (
            "Umit's dataset" if found == [other] else "the House of Commons Library's constituency results (CBP-8647)"
        )
        frame = frame.with_columns(
            votes=pl.when(chosen).then(votes).otherwise("votes"),
            printed=pl.when(chosen)
            .then(pl.lit(f"{publisher}, {title}, prints {votes:,} votes; {given} gives {other:,}."))
            .otherwise("printed"),
        )
    if frame["seats"].is_null().any():
        raise ValueError("a constituency has no seat count in any election")
    contested = ~pl.col("unopposed").all().over("date_start", "unit")
    candidates = pl.len().over("date_start", "unit")
    stv = (
        pl.col("constituency").str.contains("Universit") & pl.col("year").is_between(1918, 1945) & (pl.col("seats") > 1)
    )
    rank = pl.col("votes").rank("max", descending=True).over("date_start", "unit")
    frame = frame.with_columns(
        contested=contested,
        status=pl.when(~contested & (candidates > pl.col("seats")))
        .then(pl.lit("unopposed"))
        .when(~contested)
        .then(pl.lit("tacit"))
        .when(stv)
        .then(pl.lit("stv"))
        .when(candidates <= pl.col("seats"))
        .then(pl.lit("missing"))
        .when((rank <= pl.col("seats")).sum().over("date_start", "unit") < pl.col("seats"))
        .then(pl.lit("tie"))
        .otherwise(pl.lit("derived")),
    ).with_columns(
        elected=pl.when(pl.col("status") == "tacit")
        .then(True)
        .when(pl.col("status").is_in(["derived", "tie"]))
        .then(rank <= pl.col("seats"))
        .otherwise(False)
    )
    chosen = pl.DataFrame(
        [(date.fromisoformat(day), unit, name) for (day, unit), names in DECLARED.items() for name in names],
        schema={"date_start": pl.Date, "unit": pl.String, "candidate": pl.String},
        orient="row",
    ).with_columns(chosen=pl.lit(True))
    frame = frame.join(chosen, on=["date_start", "unit", "candidate"], how="left").with_columns(
        declared=pl.col("chosen").any().over("date_start", "unit")
    )
    if frame["chosen"].sum() != chosen.height:
        raise ValueError("a declared winner is not a candidate in Umit's dataset")
    if frame.filter(pl.col("declared") & ~pl.col("status").is_in(["stv", "missing", "tie"])).height:
        raise ValueError("a declared contest already has winners in Umit's dataset")
    frame = frame.with_columns(
        elected=pl.when("declared").then(pl.col("chosen").fill_null(False)).otherwise("elected")
    ).drop("chosen")
    member = (
        frame.filter(pl.col("mp_id_mnis").is_not_null())
        .group_by("unit", "candidate")
        .agg(inherited=pl.col("mp_id_mnis").unique())
        .filter(pl.col("inherited").list.len() == 1)
        .with_columns(pl.col("inherited").list.first())
    )
    frame = frame.join(member, on=["unit", "candidate"], how="left").with_columns(
        member=pl.coalesce("mp_id_mnis", "inherited")
    )
    frame = frame.sort("date_start", "unit", "votes", descending=[False, False, True]).with_columns(
        suffix=pl.int_range(pl.len()).over("date_start", "unit", "candidate")
    )
    frame = frame.with_columns(
        member=pl.when(pl.col("suffix") == 0).then("member"),
        suffix=pl.when(pl.col("suffix") > 0).then(pl.format("~{}", pl.col("suffix") + 1)).otherwise(pl.lit("")),
    )
    for year, unit, candidate in DUPLICATES:
        twice = (pl.col("year") == year) & (pl.col("unit") == unit) & (pl.col("candidate") == candidate)
        if frame.filter(twice).height != 1:
            raise ValueError(f"{candidate} does not stand in {unit} in {year}")
        frame = frame.filter(~twice)
    for unit, candidate, first, last, mark in NAMESAKES:
        chosen = (pl.col("unit") == unit) & (pl.col("candidate") == candidate) & pl.col("year").is_between(first, last)
        if frame.filter(chosen).height == 0:
            raise ValueError(f"{candidate} does not stand in {unit} from {first} to {last}")
        frame = frame.with_columns(
            suffix=pl.when(chosen).then(pl.concat_str("suffix", pl.lit(mark))).otherwise("suffix"),
            member=pl.when(chosen).then("mp_id_mnis").otherwise("member"),
        )
    registers = library()
    found: dict[tuple[date, str], int] = {}
    contests = frame.group_by("date_start", "year", "unit").agg(pl.col("constituency").first())
    for (sheet, year), sheet_rows in registers.group_by("sheet", "year"):
        keyed = [(words(name), count) for name, count in sheet_rows.select("name", "electorate").iter_rows()]
        day = LIBRARY_SHEETS.get(sheet) or contests.filter(pl.col("year") == year)["date_start"].min()
        for held_on, _, unit, constituency in contests.filter(pl.col("date_start") == day).iter_rows():
            own = words(constituency)
            hits = [count for key, count in keyed if key == own] or [count for key, count in keyed if own <= key]
            if len(hits) == 1:
                found[(held_on, unit)] = hits[0]
    frame = frame.join(
        pl.DataFrame(
            [(day, unit, count) for (day, unit), count in found.items()],
            schema={"date_start": pl.Date, "unit": pl.String, "registered": pl.Int64},
            orient="row",
        ),
        on=["date_start", "unit"],
        how="left",
    )
    ratio = (pl.col("electorate") / pl.col("registered")).log10()
    turnout = pl.col("votes_valid") / pl.col("electorate")
    better = pl.col("votes_valid") / pl.col("registered")
    single = (pl.col("seats") == 1) & pl.col("contested")
    frame = frame.with_columns(
        registered=pl.when(pl.col("electorate").is_null())
        .then("registered")
        .when(pl.col("electorate") == pl.col("registered"))
        .then(None)
        .when(((ratio - ratio.round()).abs() < 0.004) & (ratio.round() != 0))
        .then("registered")
        .when(single & ((turnout > 1) | (turnout < 0.15)) & better.is_between(0.25, 1))
        .then("registered")
    )
    frame = frame.with_columns(
        correction=pl.concat_str(
            "correction",
            pl.when(pl.col("registered").is_not_null() & pl.col("electorate").is_not_null()).then(
                pl.format(
                    "The electorate is the House of Commons Library's figure ({}); Umit's dataset gives {}.",
                    pl.col("registered").map_elements(lambda count: f"{count:,}", return_dtype=pl.String),
                    pl.col("electorate").map_elements(lambda count: f"{count:,}", return_dtype=pl.String),
                )
            ),
            separator=" ",
            ignore_nulls=True,
        ),
        revised=pl.col("correction").is_not_null()
        | pl.col("registered").is_not_null()
        | pl.col("printed").is_not_null().any().over("date_start", "unit"),
        electorate=pl.coalesce("registered", "electorate"),
        library=pl.col("registered").is_not_null(),
    ).with_columns(correction=pl.when(pl.col("correction") != "").then("correction"))
    for day, unit, candidate, party in ALLIANCE:
        chosen = (pl.col("date_start") == date.fromisoformat(day)) & (pl.col("unit") == unit)
        if (
            frame.filter(
                chosen & (pl.col("candidate") == candidate) & (pl.col("party") == "SDP-Liberal Alliance")
            ).height
            != 1
        ):
            raise ValueError(f"{candidate} is not the Alliance candidate in {unit} on {day}")
        frame = frame.with_columns(
            party=pl.when(chosen & (pl.col("candidate") == candidate)).then(pl.lit(party)).otherwise("party"),
            correction=pl.when(chosen)
            .then(
                pl.concat_str(
                    "correction",
                    pl.lit(
                        f"Umit's dataset labels {candidate.partition(', ')[2]} {candidate.partition(',')[0]} with the "
                        f"SDP–Liberal Alliance; he sat for the {party} Party, one of its two parties (Wikidata, "
                        "parliamentary group)."
                    ),
                    separator=" ",
                    ignore_nulls=True,
                )
            )
            .otherwise("correction"),
        )
    print(f"{frame.filter('library').select('date_start', 'unit').n_unique()} electorates from the Commons Library")
    clashes = frame.group_by("date_start", "constituency").agg(pl.col("unit").n_unique()).filter(pl.col("unit") > 1)
    if clashes.height:
        raise ValueError(f"constituency names repeat within an election: {clashes}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        "election",
        "year",
        "date_start",
        "date_end",
        "country",
        "unit",
        "constituency",
        "constituency_type",
        "seats",
        "electorate",
        "votes_valid",
        "votes_invalid",
        "party",
        "candidate",
        "name",
        "surname",
        "gender",
        "member",
        "suffix",
        "votes",
        "contested",
        "status",
        "elected",
        "declared",
        "notes",
        "correction",
        "revised",
        "library",
        "printed",
    ).sort("date_start", "unit", pl.col("votes"), descending=[False, False, True]).write_parquet(
        PARSED / "results.parquet"
    )
    print(frame.group_by("status").agg(pl.col("unit").n_unique()).sort("status"))
    short = (
        frame.group_by("date_start", "unit")
        .agg(pl.col("seats").first(), pl.col("elected").sum())
        .group_by("date_start")
        .agg(seats=pl.col("seats").sum(), elected=pl.col("elected").sum(), contests=pl.len())
        .filter(pl.col("seats") != pl.col("elected"))
        .sort("date_start")
    )
    print(f"general elections whose elected candidates differ from their seats: {short.rows()}")
    print(f"{frame.height} candidacies in {frame['date_start'].n_unique()} elections")


def emit() -> Path:
    rows = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Commons general elections 1832–2024 by constituency, from Umit's candidate-level results",
            polity="GB",
        )
        zeroed = cs.zeroed()
        survivors = set(cs.survivors.values())
        named: dict[UUID, list[tuple[UUID, str]]] = {}
        for id, entity, text in conn.execute(
            "select n.id, n.entity_id, n.text from name n join changeset c on c.id = n.changeset_id"
            " where c.pipeline = %s",
            [SOURCE],
        ):
            named.setdefault(entity, []).append((id, text))
        sitting = {
            entity
            for (entity,) in conn.execute(
                "select n.entity_id from name n join changeset c on c.id = n.changeset_id"
                " where c.pipeline = 'gb.mnis' and n.is_preferred"
            ).fetchall()
        }
        cautions: dict[UUID, list[tuple[UUID, str]]] = {}
        for id, entity, text in conn.execute(
            "select n.id, n.entity_id, n.body_md from note n join changeset c on c.id = n.changeset_id"
            " where c.pipeline = %s and n.kind = 'caveat'",
            [SOURCE],
        ):
            cautions.setdefault(entity, []).append((id, text))
        contexts: dict[UUID, list[tuple[UUID, str]]] = {}
        for id, entity, text in conn.execute(
            "select n.id, n.entity_id, n.body_md from note n join changeset c on c.id = n.changeset_id"
            " join contest k on k.id = n.entity_id where c.pipeline = %s and n.kind = 'context'",
            [SOURCE],
        ):
            contexts.setdefault(entity, []).append((id, text))

        def name(entity: UUID, text: str, start: date | None = None) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang="en",
                kind="official",
                is_preferred=True,
                valid_from=start,
            )

        gb = cs.polity("GB")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            attribution="Umit, Resul. UK House of Commons Election Results at Candidate Level. Harvard Dataverse.",
            notes=NOTES,
        )
        cs.artifact(raw.cached(SOURCE, RESULTS), source)
        cs.artifact(raw.cached(SOURCE, README), source)
        articles = cs.source("Wikipedia", ARTICLES_TITLE)
        cs.add(
            "source",
            id=articles,
            kind="community",
            publisher="Wikipedia",
            title=ARTICLES_TITLE,
            url="https://en.wikipedia.org/wiki/Category:Parliamentary_constituencies_in_the_United_Kingdom",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Party labels from Wikipedia's UK Parliament constituency articles, CC BY-SA 4.0.",
            notes="Each article's election boxes cite Craig's British Parliamentary Election Results 1832–1885 or "
            "Walker's Parliamentary Election Results in Ireland 1801–1922 for the candidates' parties.",
        )
        election_articles = cs.source("Wikipedia", ELECTIONS_TITLE)
        cs.add(
            "source",
            id=election_articles,
            kind="community",
            publisher="Wikipedia",
            title=ELECTIONS_TITLE,
            url="https://en.wikipedia.org/wiki/List_of_United_Kingdom_general_elections",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            notes="Each article's results table gives Craig's national seat and vote totals.",
        )
        alliance_source = cs.source("Wikipedia", ALLIANCE_TITLE)
        cs.add(
            "source",
            id=alliance_source,
            kind="community",
            publisher="Wikipedia",
            title=ALLIANCE_TITLE,
            url=ALLIANCE_URL,
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            notes="The Liberal Party and the Social Democratic Party fought the 1983 and 1987 general elections as one "
            "alliance, with one candidate from either party in each seat in Great Britain.",
        )
        retiring_slugs = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s join external_id x on x.entity_id = s.entity_id
                   join entity e on e.id = s.entity_id and e.retired_by is null
                   where x.scheme = 'gb.contest' and x.value = any(%s)""",
                [[f"{day}:{unit}" for day, unit in SPLIT]],
            ).fetchall()
        }
        compact_source = cs.source("Wikipedia", COMPACT_TITLE)
        cs.add(
            "source",
            id=compact_source,
            kind="community",
            publisher="Wikipedia",
            title=COMPACT_TITLE,
            url=COMPACT_URL,
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            notes="The February 1835 agreement between the Whigs, Radicals, and O'Connell's Irish Repealers, after the "
            "January 1835 general election, under which Repealers stood with Whig support in 1837.",
        )
        commons_library = cs.source("House of Commons Library", LIBRARY_TITLE)
        cs.add(
            "source",
            id=commons_library,
            kind="official_body",
            publisher="House of Commons Library",
            title=LIBRARY_TITLE,
            url=LIBRARY_PAGE,
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="Read through the Internet Archive's copy of the workbook, since the Library's file host refuses scripts.",
        )
        cs.artifact(raw.cached(SOURCE, LIBRARY), commons_library)
        printed_sources = {}
        for tag, (publisher, title, url) in PRINTED.items():
            printed_sources[tag] = cs.source(publisher, title)
            cs.add("source", id=printed_sources[tag], kind="official_body", publisher=publisher, title=title, url=url)
            cs.artifact(raw.cached(SOURCE, url), printed_sources[tag])

        old = cs.area("gb.nation", "GBI")
        if old in cs.minted:
            raise ValueError("publish gb.parliament before gb.umit")
        nation = cs.area("gb.nation", "GBR")
        if nation in cs.minted:
            cs.add("area", id=nation, scheme_code="gb.nation")
            cs.add("area_code", area_id=nation, code="GBR", valid_from=PARTITION)
            name(nation, "United Kingdom of Great Britain and Northern Ireland")
            cs.add("polity_territory", polity_id=gb, area_id=nation, valid_from=PARTITION)
        countries = {code: cs.area("gb.country", code) for code in COUNTRIES.values()}
        if countries["NIR"] in cs.minted:
            cs.add("area", id=countries["NIR"], scheme_code="gb.country")
            cs.add("area_code", area_id=countries["NIR"], code="NIR", valid_from=PARTITION)
            cs.add("area_parent", area_id=countries["NIR"], parent_id=nation, valid_from=PARTITION)
            name(countries["NIR"], "Northern Ireland")
            for code in ("ENG", "WLS", "SCT"):
                cs.add("area_code", area_id=countries[code], code=code, valid_from=PARTITION)
                cs.add("area_parent", area_id=countries[code], parent_id=nation, valid_from=PARTITION)

        districts = {
            (fold(text), country, start): area
            for text, country, start, area in conn.execute(
                """select n.text, k.code, lower(c.valid), a.id from area a
                   join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   join area_parent ap on ap.area_id = a.id and ap.valid && c.valid
                   join area_code p on p.area_id = ap.parent_id
                   join lateral (
                       select coalesce(
                           (select r.code from area_parent rp join area_code r on r.area_id = rp.parent_id
                            where rp.area_id = ap.parent_id and p.code ~ '^E12' limit 1),
                           p.code
                       ) as code
                   ) k on true
                   join changeset x on x.id = a.changeset_id and x.pipeline <> 'gb.university_seats'
                   where a.scheme_code = 'gb.pcon' and lower(c.valid) is not null and c.code !~ '^[0-9]{4}:'"""
            ).fetchall()
        }
        shaped = sorted({start for _, _, start in districts})

        commons = cs.key("gb.body", "house-of-commons")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block", "limited", "stv")}
        if commons in cs.minted or systems["fptp"] in cs.minted:
            raise ValueError("publish gb.parliament before gb.umit")
        if systems["stv"] in cs.minted:
            cs.add("electoral_system", id=systems["stv"], polity_id=gb, rules={"formula": "stv"}, rules_version=1)
            name(systems["stv"], STV)

        parties: dict[str, UUID] = {}

        def party_of(label: str, year: int, country: str) -> UUID | None:
            if label in NOT_PARTIES:
                return None
            key = (
                "speaker"
                if label == "Speaker"
                else next(
                    (key for (era, since), key in sorted(ERAS.items(), reverse=True) if era == label and year >= since),
                    None,
                )
            )
            if key is None and label == "Unionist":
                key = "scottish-unionist" if country == "SCT" else "conservative"
            key = key or ALIASES.get(label) or slug(label)
            if key not in parties:
                if key in EXISTING:
                    parties[key] = cs.key(*EXISTING[key])
                    if parties[key] in cs.minted:
                        raise ValueError(f"{key} is not in the database")
                else:
                    parties[key] = cs.key("gb.party", key)
                    if parties[key] in cs.minted:
                        cs.add(
                            "party",
                            id=parties[key],
                            kind="independent_group" if key == "speaker" else "party",
                            polity_id=gb,
                        )
                        name(parties[key], NAMES.get(key, label))
                if key == "liberal-national":
                    cs.add(
                        "name",
                        entity_id=parties[key],
                        text="Liberal National Party",
                        lang="en",
                        kind="historical",
                        is_preferred=False,
                    )
                if key == "reform-uk":
                    registered = cs.source("Electoral Commission", "Register of political parties: Reform UK (PP7931)")
                    cs.add(
                        "source",
                        id=registered,
                        kind="official_body",
                        publisher="Electoral Commission",
                        title="Register of political parties: Reform UK (PP7931)",
                        url=REFORM_PAGE,
                        notes="Registered on 5 February 2019 as The Brexit Party; the change of name to Reform UK was "
                        "authorized on 6 January 2021.",
                    )
                    cs.artifact(raw.cached(SOURCE, REFORM), registered)
                    brexit = conn.execute(
                        """select n.id from name n join changeset c on c.id = n.changeset_id and c.pipeline = %s
                           where n.entity_id = %s and n.text = 'The Brexit Party'""",
                        [SOURCE, parties[key]],
                    ).fetchone()
                    cs.add(
                        "name",
                        **({"id": brexit[0]} if brexit is not None else {}),
                        source_id=registered,
                        entity_id=parties[key],
                        text="The Brexit Party",
                        lang="en",
                        kind="official",
                        is_preferred=False,
                        valid_from=date(2019, 2, 5),
                        valid_to=date(2021, 1, 6),
                    )
            return parties[key]

        universities = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
                   join changeset x on x.id = a.changeset_id and x.pipeline = 'gb.university_seats'
                   where a.scheme_code = 'gb.pcon'"""
            ).fetchall()
        )
        units: dict[str, UUID] = {}
        for unit_code, country, label in (
            rows.sort("date_start").group_by("unit", maintain_order=True).agg(pl.col("country", "constituency").last())
        ).iter_rows():
            units[unit_code] = cs.unit("gb.pcon", unit_code)
            minted = units[unit_code] in cs.minted
            if minted or unit_code in universities:
                cs.add(
                    "reporting_unit",
                    id=units[unit_code],
                    polity_id=gb,
                    scheme_code="gb.pcon",
                    code=unit_code,
                    area_relation="equals" if unit_code in universities else "within",
                    area_id=universities.get(unit_code, countries[country]),
                    kind="standard",
                    valid_from=None,
                )
            if minted:
                name(units[unit_code], label)

        era = (
            pl.col("date_start")
            .dt.year()
            .replace_strict(
                {year: max(since for since in REDISTRIBUTIONS if since <= year) for year in range(1832, 2025)}
            )
        )
        starts = sorted(rows["date_start"].unique())
        following = dict(itertools.pairwise(starts))
        reformed = {
            (fold(text), country): area
            for text, country, area in conn.execute(
                """select distinct on (n.text, k.code) n.text, k.code, a.id from area a
                   join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   join area_parent ap on ap.area_id = a.id
                   join area_code p on p.area_id = ap.parent_id
                   join lateral (
                       select coalesce(
                           (select r.code from area_parent rp join area_code r on r.area_id = rp.parent_id
                            where rp.area_id = ap.parent_id and p.code ~ '^E12' limit 1),
                           p.code
                       ) as code
                   ) k on true
                   where a.scheme_code = 'gb.pcon' and lower(c.valid) is null
                   order by n.text, k.code, a.id"""
            ).fetchall()
        }
        plans = dict(conn.execute("select vintage, id from boundary_set where scheme_code = 'gb.pcon'").fetchall())
        historic: dict[tuple[int, str], UUID] = {}
        for since, unit_code, country, label, first, last in (
            rows.with_columns(era=era)
            .filter(
                ~pl.col("era").is_in([1868, 1997]),
                ~pl.col("constituency").str.contains("Universit"),
            )
            .sort("date_start")
            .group_by("era", "unit", maintain_order=True)
            .agg(
                pl.col("country").last(),
                pl.col("constituency").last(),
                pl.col("date_start").first().alias("first"),
                pl.col("date_start").last().alias("last"),
            )
            .iter_rows()
        ):
            label = MISSPELLED.get(label, label)
            if country == "SCT":
                label = label.replace(" Boroughs", " Burghs")
            code = f"{since}:{unit_code}"
            area = historic[(since, unit_code)] = cs.area("gb.pcon", code, on=first)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.pcon")
                cs.add("area_code", area_id=area, code=code, valid_from=first, valid_to=following[last])
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=countries[country],
                    valid_from=first,
                    valid_to=following[last],
                )
                name(area, label)
        for index, since in enumerate(REDISTRIBUTIONS[:-1]):
            if since == 1868:
                continue
            opening = min(day for day in starts if day.year >= since)
            closing = min(day for day in starts if day.year >= REDISTRIBUTIONS[index + 1])
            cs.add(
                "boundary_set",
                **({"id": plans[str(since)]} if str(since) in plans else {}),
                scheme_code="gb.pcon",
                vintage=str(since),
                valid_from=opening,
                valid_to=closing,
                as_of=opening,
            )

        spans: dict[tuple[str, UUID], list[date]] = {}
        persons: dict[str, UUID] = {}
        for identity, (scheme, value) in SAME_PERSON.items():
            duplicate, into = cs.key(f"{SOURCE}.person", identity), cs.key(scheme, value)
            if duplicate in cs.minted or into in cs.minted:
                raise ValueError(f"{identity} or {scheme}:{value} is not in the database")
            if conn.execute("select from entity_merge where entity_id = %s", [duplicate]).fetchone() is None:
                cs.merge(duplicate, into)
            persons[identity] = into
        restaged: set[UUID] = set()
        relabeled: set[tuple[date, str, str]] = set()
        early_seen: set[tuple[date, str, str]] = set()
        for (start, end, label, year), held in rows.group_by(
            "date_start", "date_end", "election", "year", maintain_order=True
        ):
            title = f"{label.removesuffix(' General Election')} United Kingdom general election"
            election = cs.key("gb.election", str(start))
            cs.add(
                "election",
                id=election,
                polity_id=gb,
                kind="general",
                date_from=start,
                date_to=end,
                status="held",
            )
            name(election, title)
            path = f"gb/{start}-general"
            ucunf = None
            if start == UCUNF_DAY:
                ucunf = cs.key(f"{SOURCE}.alliance", f"{start}:ucunf")
                cs.add("alliance", id=ucunf, kind="electoral_alliance", election_id=election)
                cs.add("name", entity_id=ucunf, text=UCUNF_NAME, lang="en", kind="official", is_preferred=True)
                for member in ("Conservative", "Ulster Unionist Party"):
                    cs.add("alliance_member", alliance_id=ucunf, party_id=party_of(member, year, "NIR"), role=None)
                cs.add("citation", entity_id=ucunf, source_id=commons_library)
            if start in CRAIG_TOTALS:
                cs.add(
                    "note",
                    entity_id=election,
                    kind="context",
                    body_md=CRAIG_NOTE.format(totals=CRAIG_TOTALS[start]),
                )
                cs.add("citation", entity_id=election, source_id=election_articles)
            compact = None
            if start == COMPACT_DAY:
                compact = cs.key(f"{SOURCE}.alliance", f"{start}:lichfield-house")
                cs.add("alliance", id=compact, kind="electoral_alliance", election_id=election)
                cs.add("name", entity_id=compact, text=COMPACT_NAME, lang="en", kind="official", is_preferred=True)
                for member in ("Repeal Association", "Whig"):
                    cs.add("alliance_member", alliance_id=compact, party_id=party_of(member, year, "IRL"), role=None)
                cs.add("citation", entity_id=compact, source_id=compact_source)
            alliance = None
            if start in ALLIANCE_DAYS:
                alliance = cs.key(f"{SOURCE}.alliance", f"{start}:sdp-liberal")
                cs.add("alliance", id=alliance, kind="electoral_alliance", election_id=election)
                cs.add("name", entity_id=alliance, text=ALLIANCE_NAME, lang="en", kind="official", is_preferred=True)
                for member in ("Liberal", "Social Democratic"):
                    cs.add("alliance_member", alliance_id=alliance, party_id=party_of(member, year, "ENG"), role=None)
                cs.add("citation", entity_id=alliance, source_id=alliance_source)
            cs.add("slug", path=path, entity_id=election)
            for (unit_code,), lines in held.group_by("unit", maintain_order=True):
                first = lines.row(0, named=True)
                seats = first["seats"]
                status = first["status"]
                contest_key = f"{start}:{unit_code}"
                contest = cs.key("gb.contest", contest_key)
                system = (
                    "stv"
                    if status == "stv"
                    else "fptp"
                    if seats == 1
                    else "limited"
                    if seats >= 3 and 1868 <= year <= 1880
                    else "block"
                )
                tacit = status in ("tacit", "unopposed")
                since = max((day for day in shaped if day <= start), default=None)
                constituency = first["constituency"]
                district = (
                    universities[unit_code]
                    if unit_code in universities
                    else reformed.get(
                        (
                            fold(
                                MISSPELLED.get(constituency, constituency).replace(
                                    " Boroughs", " Burghs" if first["country"] == "SCT" else " Boroughs"
                                )
                            ),
                            first["country"],
                        )
                    )
                    if 1868 <= year < 1885
                    else historic.get((max(since for since in REDISTRIBUTIONS if since <= year), unit_code))
                    if since is None
                    else districts.get((fold(RENAMED.get(constituency, constituency)), first["country"], since))
                )
                if since is not None and district is None and not (first["country"] == "NIR" and start < SHAPED):
                    raise ValueError(f"{start}: no boundary for {constituency}")
                if district is None and "Universit" not in constituency and start < SHAPED:
                    print(f"{start}: no area for {constituency}")
                if district is not None:
                    spans.setdefault((unit_code, district), []).append(start)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=commons,
                    district_area_id=district,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems[system],
                    default_result_kind="tacit" if tacit else "counted",
                )
                shown = MISSPELLED.get(constituency, constituency)
                name(contest, f"{label.removesuffix(' General Election')} general election in {shown}")
                if f"{path}/{slug(shown)}" not in retiring_slugs:
                    cs.add("slug", path=f"{path}/{slug(shown)}", entity_id=contest)
                notes = {
                    note
                    for note in set(lines["notes"].drop_nulls()) | set(lines["correction"].drop_nulls())
                    if note.strip() and not WORKING_NOTE.search(note)
                }
                for note in sorted(notes):
                    cs.add("note", entity_id=contest, kind="context", body_md=note)
                for id, text in contexts.get(contest, []):
                    if text not in notes:
                        cs.add(
                            "note_drop",
                            note_id=id,
                            entity_id=contest,
                            body_md=text,
                            reason="The note is empty, a compiler's working note, or replaced by a corrected one.",
                        )
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
                fresh = result_set in cs.minted or result_set in zeroed
                caveat = (
                    COUNTED_CAVEAT
                    if (str(start), unit_code) in COUNTED
                    else HANSARD_CAVEAT
                    if (str(start), unit_code) in HANSARD
                    else DECLARED_CAVEAT
                    if first["declared"]
                    else {
                        "derived": DERIVED_CAVEAT,
                        "tie": TIE_CAVEAT,
                        "stv": STV_CAVEAT,
                        "missing": MISSING_CAVEAT,
                        "unopposed": UNOPPOSED_CAVEAT,
                    }.get(status)
                )
                if caveat is not None:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=caveat)
                for id, text in cautions.get(result_set, []):
                    if text != caveat:
                        cs.add(
                            "note_drop",
                            note_id=id,
                            entity_id=result_set,
                            body_md=text,
                            reason="The caveat no longer describes this result set after a correction to the seat count "
                            "or the winners.",
                        )
                if fresh or first["revised"]:
                    if first["library"]:
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=units[unit_code],
                            option_id=None,
                            channel=0,
                            measure=REGISTERED,
                            count_round=0,
                            source_id=commons_library,
                        )
                    for measure, value in (
                        (REGISTERED, first["electorate"]),
                        (VALID, first["votes_valid"] if seats == 1 and not tacit else None),
                        (INVALID, first["votes_invalid"] if not tacit else None),
                    ):
                        if value:
                            cs.add(
                                "result_value",
                                result_set_id=result_set,
                                unit_id=units[unit_code],
                                option_id=None,
                                channel=0,
                                measure=measure,
                                count_round=0,
                                flags=0,
                                value=value,
                            )
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{slug(line['candidate'])}{line['suffix']}"
                    option = cs.key("gb.option", option_key)
                    given: list[str] = []
                    for word in (line["name"] or "").split():
                        if not given or given[-1] != word or not word[0].isupper():
                            given.append(word)
                    if line["surname"] and " ".join(given).lower() == line["surname"].lower():
                        given = []
                    full = " ".join([*given, line["surname"] or ""]).strip() or line["candidate"]
                    identity = line["member"] or f"{unit_code}:{slug(line['candidate'])}{line['suffix']}"
                    if identity not in persons:
                        persons[identity] = (
                            cs.key("mnis", line["member"]) if line["member"] else cs.key(f"{SOURCE}.person", identity)
                        )
                        if persons[identity] in cs.minted:
                            cs.add(
                                "person",
                                id=persons[identity],
                                gender=line["gender"].lower() if line["gender"] else None,
                            )
                        if persons[identity] in cs.minted or (
                            persons[identity] in named
                            and persons[identity] not in survivors
                            and persons[identity] not in sitting
                        ):
                            name(persons[identity], full)
                        if (
                            persons[identity] in restaged
                            or persons[identity] in survivors
                            or persons[identity] in sitting
                        ):
                            stale = []
                        else:
                            stale = named.get(persons[identity], [])
                            restaged.add(persons[identity])
                        for id, text in stale:
                            if text != full:
                                cs.add(
                                    "name",
                                    id=id,
                                    entity_id=persons[identity],
                                    text=text,
                                    lang="en",
                                    kind="official",
                                    is_preferred=False,
                                    valid_from=None,
                                )
                    printed, relabel, cited = RELABELED.get(
                        (start, unit_code, line["candidate"]), (line["party"], None, False)
                    )
                    if relabel is not None:
                        relabeled.add((start, unit_code, line["candidate"]))
                    early = ARTICLE_LABELS.get((start, unit_code, line["candidate"]))
                    if early is None and line["party"] in EARLY_LABELS and year < EARLY_LABELS[line["party"]][1]:
                        early = EARLY_LABELS[line["party"]][0]
                    if early is not None:
                        printed, relabel = (
                            early,
                            ARTICLES_NOTE.format(label=line["party"], basis=ARTICLES_BASIS if year < 1900 else ""),
                        )
                        early_seen.add((start, unit_code, line["candidate"]))
                    joint = ucunf if ucunf is not None and (unit_code, line["candidate"]) in UCUNF else None
                    if compact is not None and line["party"] == "Repeal Association and Whig":
                        joint = compact
                    if alliance is not None and line["party"] in ALLIANCE_LABELS and line["country"] != "NIR":
                        joint = alliance
                    if joint is not None and joint == ucunf:
                        printed, relabel, cited = None, UCUNF_NOTE, True
                        relabeled.add((start, unit_code, line["candidate"]))
                    party = party_of(printed, year, line["country"]) if printed is not None else None
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        alliance_id=joint,
                        ballot_label=full,
                    )
                    if relabel is not None:
                        cs.add("note", entity_id=option, kind="context", body_md=relabel)
                        if cited:
                            cs.add("citation", entity_id=option, source_id=commons_library)
                        if early is not None:
                            cs.add("citation", entity_id=option, source_id=articles)
                    if line["printed"] is not None:
                        cs.add("note", entity_id=option, kind="context", body_md=line["printed"])
                        cs.add(
                            "citation",
                            entity_id=option,
                            source_id=printed_sources[RECOUNTED[(start, unit_code, line["candidate"])][2]],
                        )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.candidacy", option_key),
                        person_id=persons[identity],
                        option_id=option,
                        role="head",
                        ballot_name=line["candidate"],
                        member_of_party_id=party,
                        is_independent=line["party"] == "Independent" and joint is None,
                    )
                    if (fresh or first["revised"]) and line["contested"]:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=units[unit_code],
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=line["votes"],
                        )
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        if set(ARTICLE_LABELS) - early_seen:
            raise ValueError(f"not in Umit's dataset: {set(ARTICLE_LABELS) - early_seen}")
        expected = set(RELABELED) | {(UCUNF_DAY, unit, candidate) for unit, candidate in UCUNF}
        if relabeled != expected:
            raise ValueError(f"not in Umit's dataset: {expected - relabeled}")
        for (unit_code, district), held_on in spans.items():
            cs.add(
                "reporting_unit_area",
                unit_id=units[unit_code],
                area_id=district,
                valid_from=min(held_on),
                valid_to=following.get(max(held_on)),
            )
        orphans = conn.execute(
            """select o.id, array_remove(array_agg(y.id), null) from contest_option o
               join changeset c on c.id = o.changeset_id and c.pipeline = %s
               left join candidacy y on y.option_id = o.id
               where o.contest_id = any(%s) and o.id <> all(%s)
                 and not exists (select 1 from outcome t where t.option_id = o.id)
                 and not exists (select 1 from result_value v where v.option_k = o.k)
               group by o.id""",
            [
                SOURCE,
                list({row["contest_id"] for row in cs.rows["contest_option"]}),
                [row["id"] for row in cs.rows["contest_option"]],
            ],
        ).fetchall()
        reason = "A candidate's identity key changed, so this emit writes their option afresh; this one has no votes."
        for _, candidacies in orphans:
            for candidacy in candidacies:
                cs.add("entity_retirement", entity_id=candidacy, reason=reason)
        for option, _ in orphans:
            cs.add("entity_retirement", entity_id=option, reason=reason)
        print(f"{len(orphans)} orphaned options retired")
        for (contest,) in conn.execute(
            """select x.entity_id from external_id x join entity e on e.id = x.entity_id and e.retired_by is null
               where x.scheme = 'gb.contest' and x.value = any(%s)""",
            [[f"{day}:{unit}" for day, unit in SPLIT]],
        ).fetchall():
            cs.add(
                "entity_retirement",
                entity_id=contest,
                reason="Umit's dataset splits this election's East Sussex candidates under two spellings of the seat; "
                "they are one contest.",
            )
        groups = conn.execute(NON_PARTY, [SOURCE]).fetchall()
        for group, _ in groups:
            cs.add("party", id=group, kind="independent_group", polity_id=gb)
        print(f"{len(groups)} non-party labels marked independent_group: {', '.join(text for _, text in groups)}")
        for (placeholder,) in conn.execute(
            "select entity_id from external_id where scheme = 'gb.party' and value = 'no-label'"
        ).fetchall():
            cs.add(
                "entity_retirement",
                entity_id=placeholder,
                reason='"No label" is the source\'s mark for a candidate with no party label, not a party; the '
                "candidate now carries no party.",
            )
        if "scottish-unionist-party" in parties:
            register = cs.source(
                "Electoral Commission", "Register of political parties: Scottish Unionist Party (PP120)"
            )
            cs.add(
                "source",
                id=register,
                kind="official_body",
                publisher="Electoral Commission",
                title="Register of political parties: Scottish Unionist Party (PP120)",
                url=REGISTER_PAGE,
                notes="Registered on 28 June 2000 and deregistered on 6 November 2009; fielded candidates in Scotland.",
            )
            cs.artifact(raw.cached(SOURCE, REGISTER), register)
            cs.add("citation", entity_id=parties["scottish-unionist-party"], source_id=register)
        voice = cs.key("gb.party", "blaenau-gwent-people-s-voice")
        cs.merge(cs.key("gb.party", "peoples-voice"), voice)
        cs.add("name", entity_id=voice, text="People's Voice", lang="en", kind="short", is_preferred=False)
        return cs.write()
