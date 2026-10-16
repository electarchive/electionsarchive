import io
import json
import re
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import polars as pl
import shapefile
from pyproj import CRS

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.wards"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCGIS = "/query?where=1%3D1&outFields=*&outSR=4326&f=geojson&resultOffset={offset}&resultRecordCount={count}"
LAST = 2026
FREE = "No license stated; free download from the city's open data portal"
PUBLISHERS = {
    "chicago": (
        "City of Chicago",
        "City of Chicago Data Portal Terms of Use",
        "https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html",
    ),
    "granit": (
        "New Hampshire Office of Planning and Development",
        "Public record; GRANIT lists no access or use constraints beyond not for legal use",
        None,
    ),
    "nyc": (
        "New York City Department of City Planning",
        "Public record; free download with DCP's disclaimer of warranty",
        None,
    ),
    "albuquerque": ("City of Albuquerque", FREE, None),
    "atlanta": ("City of Atlanta Department of City Planning", FREE, None),
    "augusta": ("Augusta-Richmond County", FREE, None),
    "berkeley": ("City of Berkeley", FREE, None),
    "boston": (
        "City of Boston",
        "Open Data Commons Public Domain Dedication and License (PDDL)",
        "https://opendatacommons.org/licenses/pddl/1-0/",
    ),
    "buffalo": ("City of Buffalo", FREE, None),
    "cary": ("Town of Cary", "CC0 1.0 Universal", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "cleveland": (
        "City of Cleveland",
        "Open Database License (ODbL) 1.0",
        "https://opendatacommons.org/licenses/odbl/1-0/",
    ),
    "colorado-springs": ("City of Colorado Springs", FREE, None),
    "dallas": ("City of Dallas", FREE, None),
    "davie": ("Town of Davie", FREE, None),
    "denver": ("City and County of Denver", FREE, None),
    "detroit": ("City of Detroit", FREE, None),
    "downey": ("City of Downey", FREE, None),
    "elk-grove": ("City of Elk Grove", FREE, None),
    "evansville": ("City of Evansville", FREE, None),
    "everett": ("City of Everett", FREE, None),
    "fort-worth": (
        "City of Fort Worth",
        "Creative Commons Attribution 4.0",
        "https://creativecommons.org/licenses/by/4.0/",
    ),
    "fresno": ("City of Fresno", FREE, None),
    "garland": ("City of Garland", FREE, None),
    "hanford": ("City of Hanford", FREE, None),
    "kansas-city": ("City of Kansas City, Missouri", FREE, None),
    "kenosha": ("City of Kenosha", FREE, None),
    "la": ("City of Los Angeles", FREE, None),
    "la-county": ("Los Angeles County Department of Public Works", FREE, None),
    "laredo": ("City of Laredo", FREE, None),
    "louisville": ("Louisville/Jefferson County Information Consortium", FREE, None),
    "madison": ("City of Madison", FREE, None),
    "memphis": ("City of Memphis", FREE, None),
    "milwaukee": ("Milwaukee County", FREE, None),
    "minneapolis": (
        "City of Minneapolis",
        "Public domain; the city waives all copyright and related rights to its open data",
        None,
    ),
    "moreno-valley": ("City of Moreno Valley", FREE, None),
    "nashville": (
        "Metropolitan Government of Nashville and Davidson County",
        "Metro Nashville Open Data policy (Executive Order FO-018)",
        None,
    ),
    "new-orleans": ("City of New Orleans", "Public domain", None),
    "oakland": ("City of Oakland", FREE, None),
    "omaha": ("Douglas County", FREE, None),
    "orlando": ("City of Orlando", FREE, None),
    "pasadena": ("City of Pasadena", FREE, None),
    "philadelphia": ("City of Philadelphia", FREE, None),
    "pittsburgh": ("City of Pittsburgh", FREE, None),
    "portland": ("City of Portland", FREE, None),
    "providence": ("City of Providence", FREE, None),
    "pueblo": ("City of Pueblo", FREE, None),
    "reno": ("City of Reno", FREE, None),
    "sacramento": ("City of Sacramento", FREE, None),
    "salt-lake-city": ("Salt Lake City", FREE, None),
    "san-antonio": ("City of San Antonio", FREE, None),
    "san-diego": ("City of San Diego", FREE, None),
    "san-francisco": ("City and County of San Francisco", FREE, None),
    "san-jose": ("City of San José", "Creative Commons Attribution", "https://creativecommons.org/licenses/by/4.0/"),
    "santa-ana": (
        "City of Santa Ana",
        "Open Data Commons Public Domain Dedication and License (PDDL)",
        "https://opendatacommons.org/licenses/pddl/1-0/",
    ),
    "seattle": ("City of Seattle", FREE, None),
    "spokane": ("City of Spokane", FREE, None),
    "tacoma": ("City of Tacoma", FREE, None),
    "tulsa": ("City of Tulsa", FREE, None),
    "wichita": ("City of Wichita", FREE, None),
    "worcester": ("City of Worcester", FREE, None),
}
NYC = "https://www.nyc.gov/assets/planning/download/zip/data-maps/open-data"
NYC_NEW = "https://s-media.nyc.gov/agencies/dcp/assets/files/zip/data-tools/bytes/city-council"
GRANIT = "https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Administrative_and_Political_Boundaries/d-nhpolitdists"
PLANS: dict[str, list[tuple[int, int | None, str, str, str, str]]] = {
    "3345140": [
        (
            2013,
            2023,
            f"{GRANIT}/NHPolitDists.zip",
            r"WARD~Manchester\s*-?\s*Ward (\d+)",
            "granit",
            "NH Political Districts (2012 wards)",
        ),
        (
            2023,
            None,
            f"{GRANIT}/2022/NHPolitDists2022.zip",
            r"DISTRICT~Manchester\s*-?\s*Ward (\d+)",
            "granit",
            "NH Political Districts 2022",
        ),
    ],
    "3350260": [
        (
            2013,
            2023,
            f"{GRANIT}/NHPolitDists.zip",
            r"WARD~Nashua\s*-?\s*Ward (\d+)",
            "granit",
            "NH Political Districts (2012 wards)",
        ),
        (
            2023,
            None,
            f"{GRANIT}/2022/NHPolitDists2022.zip",
            r"DISTRICT~Nashua\s*-?\s*Ward (\d+)",
            "granit",
            "NH Political Districts 2022",
        ),
    ],
    "1714000": [
        (2003, 2015, "socrata:data.cityofchicago.org/72b6-8fe8", "ward", "chicago", "Boundaries - Wards (2003-2015)"),
        (2015, 2023, "socrata:data.cityofchicago.org/k9yb-bpqx", "ward", "chicago", "Boundaries - Wards (2015-2023)"),
        (2023, None, "socrata:data.cityofchicago.org/p293-wvbd", "ward", "chicago", "Boundaries - Wards (2023-)"),
    ],
    "3651000": [
        (
            2003,
            2013,
            f"{NYC}/nycc_12c.zip",
            "CounDist",
            "nyc",
            "City Council Districts (Clipped to Shoreline), edition 12C",
        ),
        (
            2013,
            2023,
            f"{NYC}/nycc_21c.zip",
            "CounDist",
            "nyc",
            "City Council Districts (Clipped to Shoreline), edition 21C",
        ),
        (
            2023,
            None,
            f"{NYC_NEW}/nycc_25c.zip",
            "CounDist",
            "nyc",
            "City Council Districts (Clipped to Shoreline), edition 25C",
        ),
    ],
    "0644000": [
        (
            2013,
            2022,
            "arcgis:https://dpw.gis.lacounty.gov/dpw/rest/services/GIS_Web_Services/MapServer/84",
            "DISTRICT",
            "la-county",
            "LA City Council Districts (2012)",
        ),
        (
            2022,
            None,
            "https://data.lacity.org/api/geospatial/pxeu-7j74?method=export&format=GeoJSON",
            "name",
            "la",
            "LA Council Districts Dec. 2021",
        ),
    ],
    "0656000": [
        (
            1982,
            1992,
            "arcgis:https://services2.arcgis.com/zNjnZafDYCAJAbN0/arcgis/rest/services/1982_City_Council_Districts/FeatureServer/0",
            "DISTRICT_NUMBER",
            "pasadena",
            "1981 City Council Districts",
        ),
        (
            1992,
            2001,
            "arcgis:https://services2.arcgis.com/zNjnZafDYCAJAbN0/arcgis/rest/services/1992_City_Council_Districts/FeatureServer/0",
            "ID",
            "pasadena",
            "1991 City Council Districts",
        ),
        (
            2001,
            2011,
            "arcgis:https://services2.arcgis.com/zNjnZafDYCAJAbN0/arcgis/rest/services/2001_City_Council_Districts/FeatureServer/0",
            "DISTRICT",
            "pasadena",
            "2001 City Council Districts",
        ),
        (
            2011,
            2021,
            "arcgis:https://services2.arcgis.com/zNjnZafDYCAJAbN0/arcgis/rest/services/2011_City_Council_Districts/FeatureServer/0",
            "DISTRICT",
            "pasadena",
            "2011 City Council Districts",
        ),
        (
            2021,
            None,
            "arcgis:https://services2.arcgis.com/zNjnZafDYCAJAbN0/arcgis/rest/services/City_Council_Districts_view/FeatureServer/0",
            "DISTRICT",
            "pasadena",
            "City Council Districts",
        ),
    ],
    "0666000": [
        (
            2022,
            None,
            "arcgis:https://webmaps.sandiego.gov/arcgis/rest/services/Planning/PLN_Mobility/MapServer/17",
            "DISTRICT",
            "san-diego",
            "Council Districts",
        ),
    ],
    "0668000": [
        (
            1992,
            2002,
            "arcgis:https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer/541",
            "COUNCILDISTRICT",
            "san-jose",
            "City Council Districts 1991 (Archive)",
        ),
        (
            2002,
            2012,
            "arcgis:https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer/542",
            "COUNCILDISTRICT",
            "san-jose",
            "City Council Districts 2001 (Archive)",
        ),
        (
            2012,
            2022,
            "arcgis:https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer/543",
            "DISTRICT",
            "san-jose",
            "City Council Districts 2011 (Archive)",
        ),
        (
            2022,
            None,
            "arcgis:https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer/120",
            "DISTRICT",
            "san-jose",
            "City Council Districts (Current)",
        ),
    ],
    "0664000": [
        (
            2012,
            2022,
            "arcgis:https://services5.arcgis.com/54falWtcpty3V47Z/arcgis/rest/services/Council_Districts_2011/FeatureServer/0",
            "DISTNUM",
            "sacramento",
            "Council Districts 2011",
        ),
        (
            2022,
            None,
            "arcgis:https://services5.arcgis.com/54falWtcpty3V47Z/arcgis/rest/services/Council_Districts/FeatureServer/0",
            "DISTNUM",
            "sacramento",
            "Council Districts",
        ),
    ],
    "0653000": [
        (
            2023,
            None,
            "arcgis:https://services.arcgis.com/9tC74aDHuml0x5Yz/arcgis/rest/services/Council_Districts_2022/FeatureServer/0",
            "NAME",
            "oakland",
            "Council Districts 2022",
        ),
    ],
    "0667000": [
        (
            2022,
            None,
            "arcgis:https://services7.arcgis.com/7qUjhSdhXbHzGULA/arcgis/rest/services/Supervisor_Districts/FeatureServer/0",
            r"DName~D(\d+)",
            "san-francisco",
            "Supervisor Districts",
        ),
    ],
    "0649270": [
        (
            2014,
            2022,
            "arcgis:https://services2.arcgis.com/WgPlP3PNKC8Glejs/arcgis/rest/services/Adopted_Council_District_Boundaries_2014/FeatureServer/0",
            "DISTRICT",
            "moreno-valley",
            "Adopted Council District Boundaries 2014",
        ),
        (
            2022,
            None,
            "arcgis:https://services2.arcgis.com/WgPlP3PNKC8Glejs/arcgis/rest/services/Council_Districts/FeatureServer/0",
            "DISTRICT",
            "moreno-valley",
            "Council Districts",
        ),
    ],
    "0669000": [
        (
            2022,
            None,
            "arcgis:https://gis.santa-ana.org/server/rest/services/Public/COC_CouncilWards/FeatureServer/0",
            "WARD_NO",
            "santa-ana",
            "Council Wards",
        ),
    ],
    "0619766": [
        (
            2022,
            None,
            "arcgis:https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/District_City_of_Downey_by_Council_Districts_1/FeatureServer/1",
            "DIV_CITY",
            "downey",
            "City of Downey Council Districts",
        ),
    ],
    "0622020": [
        (
            2022,
            None,
            "arcgis:https://services1.arcgis.com/r6QlyAADl0kfhp1m/arcgis/rest/services/Council_Districts/FeatureServer/0",
            "DIST_NUM",
            "elk-grove",
            "Council Districts",
        ),
    ],
    "0631960": [
        (
            2022,
            None,
            "arcgis:https://services3.arcgis.com/xJA6NTvwhTUZnE2Z/arcgis/rest/services/2022_Council_Districts/FeatureServer/0",
            "district",
            "hanford",
            "2022 Council Districts",
        ),
    ],
    "0606000": [
        (
            2022,
            None,
            "socrata:data.cityofberkeley.info/c8zs-8y7x",
            "district",
            "berkeley",
            "City Council District Boundaries",
        ),
    ],
    "0627000": [
        (
            2022,
            None,
            "arcgis:https://services3.arcgis.com/ibgDyuD2DLBge82s/arcgis/rest/services/Fresno_City_Council_Districts/FeatureServer/0",
            "COUNCIL_DISTRICT",
            "fresno",
            "Fresno City Council Districts",
        ),
    ],
    "2148000": [
        (
            2003,
            2012,
            "arcgis:https://gis.lojic.org/maps/rest/services/LojicSolutions/OpenDataPolitical/MapServer/9",
            "COUNDIST",
            "louisville",
            "Metro Council Districts, original in 2003",
        ),
        (
            2012,
            2022,
            "arcgis:https://gis.lojic.org/maps/rest/services/LojicSolutions/OpenDataPolitical/MapServer/10",
            "COUNDIST",
            "louisville",
            "Metro Council Districts, former from 2011",
        ),
        (
            2022,
            None,
            "arcgis:https://gis.lojic.org/maps/rest/services/LojicSolutions/OpenDataPolitical/MapServer/8",
            "COUNDIST",
            "louisville",
            "Metro Council Districts",
        ),
    ],
    "5363000": [
        (
            2015,
            2023,
            "arcgis:https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/Seattle_City_Council_District_2013/FeatureServer/0",
            "C_DISTRICT",
            "seattle",
            "Seattle City Council Districts 2013",
        ),
        (
            2023,
            None,
            "arcgis:https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/Seattle_City_Council_Districts/FeatureServer/0",
            "C_DISTRICT",
            "seattle",
            "Seattle City Council Districts",
        ),
    ],
    "4260000": [
        (
            2012,
            2023,
            "arcgis:https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services/Council_Districts_2016/FeatureServer/0",
            "DISTRICT",
            "philadelphia",
            "Council Districts 2016",
        ),
    ],
    "2582000": [
        (
            2012,
            2022,
            "arcgis:https://services1.arcgis.com/j8dqo2DJE7mVUBU1/arcgis/rest/services/City_Council_Districts_Based_on_2010_Census_Data1/FeatureServer/14",
            "COUNCIL_DIST",
            "worcester",
            "City Council Districts Based on 2010 Census Data",
        ),
        (
            2022,
            None,
            "arcgis:https://services1.arcgis.com/j8dqo2DJE7mVUBU1/arcgis/rest/services/Council_Districts_2026/FeatureServer/0",
            "Council_District",
            "worcester",
            "Council Districts 2026",
        ),
    ],
    "2507000": [
        (
            2014,
            2023,
            "arcgis:https://gisportal.boston.gov/arcgis/rest/services/Planning/OpenData/MapServer/4",
            "DISTRICT",
            "boston",
            "City Council Districts 2013-2022",
        ),
        (
            2023,
            None,
            "arcgis:https://services.arcgis.com/sFnw0xNflSi8J0uh/arcgis/rest/services/CityCouncilDistricts_2023_5_25/FeatureServer/0",
            "DISTRICT",
            "boston",
            "City Council Districts 2023-2032",
        ),
    ],
    "4819000": [
        (
            2013,
            2023,
            "arcgis:https://services2.arcgis.com/rwnOSbfKSwyTBcwN/arcgis/rest/services/Dallas_Council_Districts_without_Lake_Ray_Hubbard_2ab9a/FeatureServer/0",
            "DISTRICT",
            "dallas",
            "Archived Dallas Council Districts",
        ),
        (
            2023,
            None,
            "arcgis:https://services2.arcgis.com/rwnOSbfKSwyTBcwN/arcgis/rest/services/COD_2022_Council_Districts/FeatureServer/0",
            "DistName",
            "dallas",
            "2022 Council Districts",
        ),
    ],
    "5539225": [
        (
            2012,
            2022,
            "arcgis:https://services1.arcgis.com/dEWY7aW7h9zHrSP9/arcgis/rest/services/Aldermanic_Districts/FeatureServer/0",
            "districtnumber@year=2010",
            "kenosha",
            "Aldermanic Districts",
        ),
        (
            2022,
            None,
            "arcgis:https://services1.arcgis.com/dEWY7aW7h9zHrSP9/arcgis/rest/services/Aldermanic_Districts/FeatureServer/0",
            "districtnumber@year=2020",
            "kenosha",
            "Aldermanic Districts",
        ),
    ],
    "5553000": [
        (
            2024,
            None,
            "arcgis:https://services2.arcgis.com/s1wgJQKbKJihhhaT/arcgis/rest/services/City_Common_Council_District_or_Village_Board_of_Trustees/FeatureServer/47",
            "Council_Dist@Muni_Code=53000",
            "milwaukee",
            "City Common Council District or Village Board of Trustees",
        ),
    ],
    "3611000": [
        (
            2011,
            2023,
            "arcgis:https://services8.arcgis.com/BMPgiPHUrkqJdtki/arcgis/rest/services/Council_Districts_2011/FeatureServer/0",
            "DIST_NAME",
            "buffalo",
            "Council Districts 2011",
        ),
        (
            2023,
            None,
            "arcgis:https://services8.arcgis.com/BMPgiPHUrkqJdtki/arcgis/rest/services/Council_Districts/FeatureServer/0",
            "DistrictName",
            "buffalo",
            "Council Districts",
        ),
    ],
    "4752006": [
        (
            2011,
            2023,
            "arcgis:https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/Council_Districts_(Current)/FeatureServer/0",
            "DISTRICT",
            "nashville",
            "2011 Council Districts",
        ),
        (
            2023,
            None,
            "arcgis:https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/2022_Council_Districts_(Future)_view/FeatureServer/0",
            "DISTRICT",
            "nashville",
            "2022 Council Districts",
        ),
    ],
    "0862000": [
        (
            2017,
            2021,
            "arcgis:https://services1.arcgis.com/IL17xsvNU5Bmw3RY/arcgis/rest/services/City_Council_District_Boundaries___2017_to_2021/FeatureServer/6",
            "D_CounDist",
            "pueblo",
            "City Council District Boundaries 2017 to 2021",
        ),
        (
            2021,
            None,
            "arcgis:https://services1.arcgis.com/IL17xsvNU5Bmw3RY/arcgis/rest/services/CouncilDistricts2020/FeatureServer/4",
            "D_CounDist",
            "pueblo",
            "Council Districts",
        ),
    ],
    "2622000": [
        (
            2013,
            2026,
            "arcgis:https://services2.arcgis.com/qvkbeam7Wirps6zC/arcgis/rest/services/Council_Districts/FeatureServer/0",
            "DistrictNu",
            "detroit",
            "Detroit City Council Districts",
        ),
        (
            2026,
            None,
            "arcgis:https://services2.arcgis.com/qvkbeam7Wirps6zC/arcgis/rest/services/city_council_districts_2026/FeatureServer/0",
            "district_number",
            "detroit",
            "City Council Districts 2026",
        ),
    ],
    "0820000": [
        (
            2015,
            2023,
            "arcgis:https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ADMN_COUNCILDIST2015_A/FeatureServer/0",
            "DIST_NUM",
            "denver",
            "Council Districts 2015",
        ),
    ],
    "1216475": [
        (
            2013,
            2022,
            "arcgis:https://gis.davie-fl.gov/gis/rest/services/AdminAreas/AdministrativeAreas_wm/MapServer/5",
            "DISTRICT",
            "davie",
            "Town Council Districts 2012",
        ),
        (
            2022,
            None,
            "arcgis:https://gis.davie-fl.gov/gis/rest/services/AdminAreas/AdministrativeAreas_wm/MapServer/4",
            "DISTRICT",
            "davie",
            "Town Council Districts 2021",
        ),
    ],
    "4748000": [
        (
            2023,
            None,
            "arcgis:https://services6.arcgis.com/FayuIc8kjcIuYMDI/arcgis/rest/services/DBO_Memphis_Council_Districts_2023/FeatureServer/0",
            "CD",
            "memphis",
            "Memphis Council Districts 2023",
        ),
    ],
    "2938000": [
        (
            2023,
            None,
            "arcgis:https://maps2.kcmo.org/kcgis/rest/services/Data_Layers/Council_Districts/FeatureServer/0",
            "DISTRICT",
            "kansas-city",
            "Council Districts",
        ),
    ],
    "4159000": [
        (
            2024,
            None,
            "arcgis:https://www.portlandmaps.com/od/rest/services/COP_OpenData_Boundary/MapServer/1413",
            "DISTRICT",
            "portland",
            "Portland City Council Districts",
        ),
    ],
    "3137000": [
        (
            2022,
            None,
            "arcgis:https://dcgis.org/server/rest/services/Hosted/Omaha_City_Council_Districts_(source)_view/FeatureServer/0",
            "district",
            "omaha",
            "Omaha City Council Districts",
        ),
    ],
    "2079000": [
        (
            2023,
            None,
            "arcgis:https://gismaps.wichita.gov/ageweb/rest/services/COWGIS/Districts/MapServer/3",
            "COUNCIL",
            "wichita",
            "Council Districts",
        ),
    ],
    "3502000": [
        (
            2023,
            None,
            "arcgis:https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Council_Districts/FeatureServer/0",
            "DISTRICTNUMBER",
            "albuquerque",
            "City Council Districts",
        ),
    ],
    "4967000": [
        (
            2023,
            None,
            "arcgis:https://services.arcgis.com/mMBpeYj0vPFotzbe/arcgis/rest/services/Salt_Lake_City_Council_Districts/FeatureServer/1",
            "CITYCOUNCIL_ID",
            "salt-lake-city",
            "Salt Lake City Council Districts",
        ),
    ],
    "4075000": [
        (
            2022,
            None,
            "arcgis:https://services2.arcgis.com/XkZ90iCdbTJ9oNXl/arcgis/rest/services/Council_Districts/FeatureServer/0",
            "DISTRICTID",
            "tulsa",
            "Council Districts",
        ),
    ],
    "3710740": [
        (
            2022,
            None,
            "arcgis:https://maps-apis.carync.gov/server/rest/services/Elections/Elections/FeatureServer/2",
            "DISTRICTID",
            "cary",
            "Town Council Districts",
        ),
    ],
    "5548000": [
        (
            2023,
            None,
            "arcgis:https://maps.cityofmadison.com/arcgis/rest/services/Public/OPEN_DATA/MapServer/10",
            "ALD_DIST",
            "madison",
            "Aldermanic Districts",
        ),
    ],
    "5370000": [
        (
            2023,
            None,
            "arcgis:https://services3.arcgis.com/SCwJH1pD8WSn5T5y/arcgis/rest/services/City_Council_Districts/FeatureServer/0",
            "district",
            "tacoma",
            "City Council Districts",
        ),
    ],
    "4261000": [
        (
            2023,
            None,
            "arcgis:https://services1.arcgis.com/YZCmUqbcsUpOKfj7/arcgis/rest/services/PGHWebCouncilDistricts/FeatureServer/0",
            "council_district",
            "pittsburgh",
            "City Council Districts 2022",
        ),
    ],
    "3260600": [
        (
            2022,
            None,
            "arcgis:https://services5.arcgis.com/RjM4BJrv5ZkqP4XC/arcgis/rest/services/Reno_City_Limits_and_Wards_2021/FeatureServer/0",
            "WARD_NUM",
            "reno",
            "Reno City Limits and Wards 2021",
        ),
    ],
    "4829000": [
        (
            2022,
            None,
            "arcgis:https://maps2.garlandtx.gov/server1/rest/services/BASEMAP/COUNCIL_DISTRICTS/MapServer/0",
            "DISTRICT_NUMBER",
            "garland",
            "Council Districts",
        ),
    ],
    "5367000": [
        (
            2023,
            None,
            "arcgis:https://services.arcgis.com/3PDwyTturHqnGCu0/arcgis/rest/services/City_Council/FeatureServer/2",
            "District",
            "spokane",
            "City Council Districts (2020 census)",
        ),
    ],
    "4841464": [
        (
            2022,
            None,
            "arcgis:https://services3.arcgis.com/h9QEFLHkUI1SIRs7/arcgis/rest/services/OL___Council_Districts__New_/FeatureServer/4",
            "DISTRICT",
            "laredo",
            "Council Districts",
        ),
    ],
    "4865000": [
        (
            2023,
            None,
            "arcgis:https://services.arcgis.com/g1fRTDLeMgspWrYp/arcgis/rest/services/RedistrictedCouncilDistricts2022/FeatureServer/0",
            "District",
            "san-antonio",
            "Redistricted Council Districts 2022",
        ),
    ],
    "3916000": [
        (
            2015,
            2026,
            "arcgis:https://services3.arcgis.com/dty2kHktVXHrqO8i/arcgis/rest/services/Wards/FeatureServer/0",
            "WARD",
            "cleveland",
            "City of Cleveland Wards (2014)",
        ),
        (
            2026,
            None,
            "arcgis:https://services3.arcgis.com/dty2kHktVXHrqO8i/arcgis/rest/services/Cleveland_Wards_1_2_25_Topocleaned_pop20/FeatureServer/0",
            "Ward",
            "cleveland",
            "City of Cleveland Wards (2026)",
        ),
    ],
    "4827000": [
        (
            2011,
            2023,
            "arcgis:https://mapit.fortworthtexas.gov/ags/rest/services/CIVIC/OpenData_Boundaries/MapServer/53",
            "DISTRICT",
            "fort-worth",
            "Council Districts (2010)",
        ),
        (
            2023,
            None,
            "arcgis:https://mapit.fortworthtexas.gov/ags/rest/services/CIVIC/OpenData_Boundaries/MapServer/2",
            "DISTRICT",
            "fort-worth",
            "Council Districts",
        ),
    ],
    "1253000": [
        (
            2004,
            2012,
            "socrata:data.cityoforlando.net/dtxu-3vh2",
            "comm_dist_id",
            "orlando",
            "Commission Districts 2002-2012",
        ),
    ],
    "2255000": [
        (2004, 2014, "socrata:data.nola.gov/ge7x-6fwd", "councildis", "new-orleans", "Council Districts Archive"),
        (2026, None, "socrata:data.nola.gov/d49t-wy6p", "districtid", "new-orleans", "City Council Districts"),
    ],
    "1304204": [
        (
            2003,
            2013,
            "arcgis:https://services1.arcgis.com/UKYQy2KtG5YhYPTp/arcgis/rest/services/OpenData_CommissionDistrict2002/FeatureServer/37",
            "COMMDIST",
            "augusta",
            "Historical 2002 Commission Districts",
        ),
        (
            2013,
            2022,
            "arcgis:https://services1.arcgis.com/UKYQy2KtG5YhYPTp/arcgis/rest/services/OpenData_CommissionDistricts2012/FeatureServer/36",
            "DISTRICTID",
            "augusta",
            "Historical 2012 Commission Districts",
        ),
        (
            2022,
            None,
            "arcgis:https://services1.arcgis.com/UKYQy2KtG5YhYPTp/arcgis/rest/services/OpenData_CommissionDistricts2022/FeatureServer/31",
            "DISTRICTID",
            "augusta",
            "Commission Districts",
        ),
    ],
    "2743000": [
        (
            1993,
            2003,
            "arcgis:https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/WARDS_1992/FeatureServer/0",
            "BDNUM",
            "minneapolis",
            "Wards 1992",
        ),
        (
            2003,
            2013,
            "arcgis:https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/Ward_Boundaries_2002_(Archived)/FeatureServer/0",
            "WARDS",
            "minneapolis",
            "Ward Boundaries 2002 (Archived)",
        ),
        (
            2013,
            2023,
            "arcgis:https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/WARDS/FeatureServer/0",
            "BDNUM",
            "minneapolis",
            "Wards 2012",
        ),
        (
            2023,
            None,
            "arcgis:https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/msvcWards_2022/FeatureServer/0",
            "BDNUM",
            "minneapolis",
            "Wards 2022",
        ),
    ],
    "1304000": [
        (
            2013,
            2025,
            "arcgis:https://egis.atlantaga.gov/ags/rest/services/BaseMap/CouncilDistrict/MapServer/0",
            "DISTRICT",
            "atlanta",
            "Council Districts",
        ),
    ],
    "4459000": [
        (
            2023,
            None,
            "arcgis:https://services6.arcgis.com/wv9mHoqblhTsnqdG/arcgis/rest/services/City_Council_Wards/FeatureServer/0",
            "district",
            "providence",
            "City Council Wards",
        ),
    ],
    "5322640": [
        (
            2022,
            None,
            "arcgis:https://services6.arcgis.com/z6WYi9VRHfgwgtyW/arcgis/rest/services/Everett_City_Council_Districts/FeatureServer/0",
            r"District~.*District (\d+)",
            "everett",
            "Everett City Council Districts",
        ),
    ],
    "1822000": [
        (
            2023,
            None,
            "arcgis:https://maps.evansvillegis.com/arcgis_server/rest/services/POLITICAL/CITY_COUNCIL_WARDS/MapServer/0",
            "DISPNAME",
            "evansville",
            "City Council Wards",
        ),
    ],
    "0816000": [
        (
            2025,
            None,
            "arcgis:https://services9.arcgis.com/OizVks7Qhwn2qkCY/arcgis/rest/services/2025_COS_Council_Distrcts/FeatureServer/0",
            "DISTRICT",
            "colorado-springs",
            "2025 Council Districts",
        ),
    ],
}
AREAS = {
    "0649270",
    "0656000",
    "0669000",
    "0644000",
    "0666000",
    "0668000",
    "0664000",
    "0619766",
    "0622020",
    "0631960",
    "0606000",
    "0627000",
    "0653000",
}
PREFIXES = ("council district", "district", "ward", "cd", "area")


def token(label: str) -> str:
    text = re.sub(r"\.0+$", "", str(label).strip().lower())
    for prefix in PREFIXES:
        text = text.removeprefix(prefix).strip(" -_#")
    text = re.sub(r"^(\d+)(st|nd|rd|th)\b", r"\1", text).strip(" -_")
    return text.lstrip("0") or text


def layer(url: str) -> str:
    return url.removeprefix("arcgis:")


def pages(url: str) -> Iterator[str]:
    base = layer(url)
    count = json.loads(raw.cached(SOURCE, base + "?f=json").path.read_text())["maxRecordCount"]
    offset = 0
    while True:
        link = base + ARCGIS.format(offset=offset, count=count)
        yield link
        page = json.loads(raw.cached(SOURCE, link).path.read_text())
        if len(page["features"]) < count and not page.get("properties", {}).get("exceededTransferLimit"):
            return
        offset += count


def socrata(url: str) -> str:
    domain, id = url.removeprefix("socrata:").split("/")
    return f"https://{domain}/resource/{id}.geojson?$limit=50000"


def links(url: str) -> list[str]:
    if url.startswith("arcgis:"):
        return [layer(url) + "?f=json", *pages(url)]
    return [socrata(url) if url.startswith("socrata:") else url]


def fetch() -> None:
    for plans in PLANS.values():
        for _, _, url, _, _, _ in plans:
            if url.startswith("arcgis:"):
                base = layer(url)
                count = json.loads(raw.fetch(SOURCE, base + "?f=json", suffix=".json").path.read_text())[
                    "maxRecordCount"
                ]
                offset = 0
                while True:
                    page = json.loads(
                        raw.fetch(
                            SOURCE, base + ARCGIS.format(offset=offset, count=count), suffix=".geojson"
                        ).path.read_text()
                    )
                    if len(page["features"]) < count and not page.get("properties", {}).get("exceededTransferLimit"):
                        break
                    offset += count
            else:
                raw.fetch(
                    SOURCE,
                    socrata(url) if url.startswith("socrata:") else url,
                    suffix=".zip" if url.endswith(".zip") else ".geojson",
                )


def features(url: str) -> Iterator[tuple[dict[str, object], str, int]]:
    if not url.endswith(".zip"):
        for link in links(url):
            if link.endswith("?f=json"):
                continue
            for feature in json.loads(raw.cached(SOURCE, link).path.read_text())["features"]:
                if feature["geometry"] is not None:
                    yield feature["properties"], json.dumps(feature["geometry"]), 4326
        return
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        names = {name.lower(): name for name in archive.namelist() if not name.startswith("__MACOSX")}
        for stem in sorted(name[:-4] for name in names if name.endswith(".shp")):
            reader = shapefile.Reader(
                shp=io.BytesIO(archive.read(names[f"{stem}.shp"])),
                shx=io.BytesIO(archive.read(names[f"{stem}.shx"])),
                dbf=io.BytesIO(archive.read(names[f"{stem}.dbf"])),
                encoding="latin-1",
            )
            srid = CRS(archive.read(names[f"{stem}.prj"]).decode("latin-1")).to_epsg(min_confidence=20)
            assert srid is not None, f"{url}: {stem}"
            fields = [field[0] for field in reader.fields[1:]]
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                if record is None or shape is None or shape.shapeType == shapefile.NULL or not shape.points:
                    continue
                yield dict(zip(fields, list(record), strict=True)), json.dumps(shape.__geo_interface__), srid


def parse() -> None:
    with connect() as conn:
        conn.execute(
            "create temp table feature (geoid text, plan int, label text, geojson text, srid int) on commit drop"
        )
        with (
            conn.cursor() as cursor,
            cursor.copy("copy feature (geoid, plan, label, geojson, srid) from stdin") as copy,
        ):
            for geoid, plans in PLANS.items():
                for start, _, url, field, _, _ in plans:
                    for properties, geometry, srid in features(url):
                        spec, _, condition = field.partition("@")
                        name, _, pattern = spec.partition("~")
                        key, _, value = condition.partition("=")
                        if condition and str(properties.get(key)) != value:
                            continue
                        label = properties.get(name, properties.get(name.lower()))
                        if label is not None and pattern:
                            match = re.fullmatch(pattern, str(label).strip())
                            label = match[1] if match else None
                        if label is not None and token(str(label)):
                            copy.write_row((geoid, start, token(str(label)), geometry, srid))
        rows = conn.execute(
            """with shape as (
                 select geoid, plan, label,
                        st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(st_force2d(
                          st_transform(st_setsrid(st_geomfromgeojson(geojson), srid), 4326))))), 3)) as geom
                 from feature group by geoid, plan, label
               )
               select geoid, plan, label, encode(st_asewkb(geom), 'hex'),
                      encode(st_asewkb(st_pointonsurface(geom)), 'hex'), st_area(geom::geography) / 1e6
               from shape where not st_isempty(geom)"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "geoid": pl.String,
            "plan": pl.Int32,
            "label": pl.String,
            "geom": pl.String,
            "point": pl.String,
            "km2": pl.Float64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "districts.parquet")
    print(frame.group_by("geoid", "plan").agg(pl.len(), pl.col("label").sort().str.join(" ")).sort("geoid", "plan"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "districts.parquet")
    with connect() as conn:
        areas: dict[tuple[str, str], list[str]] = {}
        for code, id in conn.execute(
            """select c.code, a.id from area a join area_code c on c.area_id = a.id
               where a.scheme_code = 'us.local_district' and split_part(c.code, ':', 2) = any(%s)
                 and split_part(c.code, ':', 3) not in ('mayor', 'board-of-education')""",
            [list(PLANS)],
        ).fetchall():
            geoid, label = code.split(":")[1], code.split(":")[3]
            if label.startswith("area-") and geoid not in AREAS:
                continue
            label = re.sub(r"-position-\d+$", "", label)
            if not re.fullmatch(r"(district|ward|area)-[0-9a-z]+|[0-9]+(st|nd|rd|th)-ward|[a-z]+", label):
                continue
            areas.setdefault((geoid, token(label.replace("-", " "))), []).append(id)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="City council districts and wards, current and past plans, from city open data portals",
            polity="US",
        )
        foreign = {
            id
            for (id,) in conn.execute(
                "select s.id from source s join changeset c on c.id = s.changeset_id where c.pipeline <> %s",
                [SOURCE],
            )
        }
        sources = {}
        for geoid, plans in PLANS.items():
            for start, _, url, _, publisher, title in plans:
                name, license, license_url = PUBLISHERS[publisher]
                source = cs.source(name, title)
                if source not in sources.values():
                    if source not in foreign:
                        cs.add(
                            "source",
                            id=source,
                            kind="official_body",
                            publisher=name,
                            title=title,
                            url=socrata(url) if url.startswith("socrata:") else layer(url),
                            license=license,
                            license_url=license_url,
                        )
                    for link in links(url):
                        cs.artifact(raw.cached(SOURCE, link), source)
                sources[(geoid, start)] = source
        years = [start for plans in PLANS.values() for start, *_ in plans]
        for year in range(min(years), LAST + 1):
            cs.add(
                "boundary_set",
                scheme_code="us.local_district",
                vintage=str(year),
                valid_from=date(year, 1, 1),
                valid_to=date(year + 1, 1, 1),
                as_of=date(year, 1, 1),
            )
        shaped = 0
        for geoid, plan, label, geom, point in frame.select("geoid", "plan", "label", "geom", "point").iter_rows():
            end = next(end for start, end, *_ in PLANS[geoid] if start == plan)
            for area in areas.get((geoid, label), []):
                shaped += 1
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=date(plan, 1, 1),
                    valid_from_precision="year",
                    valid_to=None if end is None else date(end, 1, 1),
                    valid_to_precision="year",
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=sources[(geoid, plan)],
                )
        print(f"{shaped} geometry versions on {len({id for ids in areas.values() for id in ids})} candidate areas")
        return cs.write()
