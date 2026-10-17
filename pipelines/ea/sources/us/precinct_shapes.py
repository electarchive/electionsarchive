import html
import io
import json
import re
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl
import shapefile
from pyproj import CRS

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.precinct_shapes"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2022, 11, 8)
CAUCUS = date(2020, 2, 3)
GENERAL = date(2024, 11, 5)
PUBLISHERS = {
    "MN": ("Minnesota Secretary of State", "General Election Results By Precinct 2022", "Public record", None),
    "MI": ("Michigan Department of State, Bureau of Elections", "2022 Voting Precincts", "Public record", None),
    "MD": (
        "Maryland Department of Planning",
        "2022 Maryland Precincts",
        "Free to distribute with the metadata intact",
        "State of Maryland",
    ),
    "DC": ("District of Columbia Board of Elections", "Voting Precinct", "CC BY 4.0", "DC GIS"),
    "OK": (
        "Oklahoma State Election Board",
        "Voter Precincts 2020",
        "Public record",
        "Center for Spatial Analysis, University of Oklahoma",
    ),
    "RI": ("Rhode Island Department of State", "Voting Precincts (2022)", "Public record", "RIGIS"),
    "IA": ("Iowa Secretary of State", "Iowa Precincts", "Public record", "Iowa Legislative Services Agency"),
    "MT": ("Montana State Library", "Montana Voting Precincts 2021", "Public record", None),
    "AZ": ("Maricopa County Recorder", "Maricopa County Voting Precincts (2022-)", "Public record", None),
    "NC": ("North Carolina State Board of Elections", "Precinct Shapefile, 2022-08-31", "Public record", None),
    "TX": ("Texas Legislative Council", "Precincts, 2022 General Election", "CC BY", None),
    "WA": ("Washington Secretary of State", "Statewide Precincts, 2022 General, Consolidated", "Public record", None),
    "LA": ("Louisiana Legislature", "2022 Louisiana Precincts (12-25-2022)", "Public record", None),
    "MA": ("MassGIS", "2022 Wards and Precincts", "Public record", None),
    "NM": ("New Mexico Secretary of State", "New Mexico Voting Precincts July 2024", "Public domain", "RGIS"),
    "WV": (
        "West Virginia GIS Technical Center",
        "WV Precinct 2022 (10-31-2022)",
        "Free to copy and distribute (MapWV terms of use)",
        None,
    ),
    "NH": (
        "New Hampshire Office of Planning and Development",
        "NH Political Districts 2022",
        "Public record",
        "NH GRANIT",
    ),
    "WI": (
        "Wisconsin Legislative Technology Services Bureau",
        "2022 Election Data with 2022 Wards",
        "Open and publicly available data",
        "LTSB, Wisconsin Elections Commission",
    ),
}
ARCGIS = "query?where=1%3D1&outFields=*&outSR=4326&f=geojson&resultOffset={offset}&resultRecordCount={count}"
LAYERS = {
    "MN": "https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_sos/bdry_electionresults_2022_2030/FeatureServer/1/",
    "MI": "https://gisagocss.state.mi.us/arcgis/rest/services/OpenData/boundaries/MapServer/7/",
    "MD": "https://mdgeodata.md.gov/archive/rest/services/Boundaries/MD_ArchivedElectionBoundaries/MapServer/4/",
    "DC": "https://maps2.dcgis.dc.gov/DCGIS/rest/services/DCGIS_DATA/Administrative_Other_Boundaries/MapServer/0/",
    "OK": "https://services.arcgis.com/3xOwF6p0r7IHIjfn/arcgis/rest/services/State_Wide_2020_Precincts/FeatureServer/0/",
    "RI": "https://services2.arcgis.com/S8zZg9pg23JUEexQ/ArcGIS/rest/services/BND_Voting_Precincts_2022/FeatureServer/0/",
    "IA": "https://services.arcgis.com/vPD5PVLI6sfkZ5E4/arcgis/rest/services/Iowa_Precincts/FeatureServer/0/",
    "AZ": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2022-)/FeatureServer/0/",
    "WI": "https://services1.arcgis.com/FDsAtKBk8Hy4cAH0/arcgis/rest/services/2022_Election_Data_wtih_2022_Wards/FeatureServer/0/",
    "DC:1990": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator/MapServer/30/",
    "DC:2002": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator/MapServer/29/",
    "DC:2008": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator/MapServer/28/",
    "DC:2012": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator/MapServer/45/",
    "AZ:1998-1999": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(1998-1999)/FeatureServer/0/",
    "AZ:2000-2001": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2000-2001)/FeatureServer/0/",
    "AZ:2002-2003": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2002-2003)/FeatureServer/0/",
    "AZ:2004-2005": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2004-2005)/FeatureServer/0/",
    "AZ:2006-2007": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2006-2007)/FeatureServer/0/",
    "AZ:2008-2011": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2008-2011)/FeatureServer/0/",
    "AZ:2012-2017": "https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/Maricopa_County_Voting_Precincts_(2012-2017)/FeatureServer/0/",
    "MN:2012": "https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_sos/bdry_electionresults_2012_2020/FeatureServer/4/",
    "MN:2014": "https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_sos/bdry_electionresults_2012_2020/FeatureServer/3/",
    "KY:snapshot": "https://services3.arcgis.com/ghsX9CKghMvyYjBU/arcgis/rest/services/Ky_SBE_VTD_Boundaries_WM/FeatureServer/0/",
    "AR:snapshot": "https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Boundaries/FeatureServer/11/",
    "AK:snapshot": "https://maps.commerce.alaska.gov/server/rest/services/Govt_Related/Govt_House_and_Senate_Districts/MapServer/4/",
    "UT:snapshot": "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/VistaBallotAreas/FeatureServer/0/",
    "VT:snapshot": "https://services1.arcgis.com/BkFxaEFNwHqX3tAw/ArcGIS/rest/services/FS_VCGI_VT_Municipal_Voting_Districts_SP_v1/FeatureServer/0/",
    "NJ:snapshot": "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/ArcGIS/rest/services/Election_Districts_for_New_Jersey/FeatureServer/0/",
    "ND:snapshot": "https://services1.arcgis.com/GOcSXpzwBHyk2nog/arcgis/rest/services/NDGISHUB_Voter_Precincts/FeatureServer/0/",
    "MN:2024": "https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_sos/bdry_electionresults_2022_2030/FeatureServer/0/",
    "MI:2024": "https://gisagocss.state.mi.us/arcgis/rest/services/OpenData/boundaries/MapServer/9/",
    "IN:2024": "https://gisdata.in.gov/server/rest/services/Hosted/Voting_District_Boundaries_2024/FeatureServer/1/",
    "HI:2024": "https://geodata.hawaii.gov/arcgis/rest/services/AdminBnd/MapServer/13/",
}
LAYERS |= {f"{state}:2024": LAYERS[state] for state in ("DC", "OK", "RI", "AZ", "IA")} | {
    "IN:2023": "https://gisdata.in.gov/server/rest/services/Hosted/Voting_District_Boundaries_2023/FeatureServer/1/",
    "RI:2016": "https://services2.arcgis.com/S8zZg9pg23JUEexQ/arcgis/rest/services/Voting_Precincts/FeatureServer/0/",
    "IN:marion-1990": "https://gis.indy.gov/server/rest/services/Political/VotingPrecincts/MapServer/1/",
    "IN:marion-2011": "https://gis.indy.gov/server/rest/services/Political/VotingPrecincts/MapServer/2/",
    "IN:marion-2021": "https://gis.indy.gov/server/rest/services/Political/VotingPrecincts/MapServer/3/",
    "IL:snapshot": "https://services3.arcgis.com/HESxeTbDliKKvec2/arcgis/rest/services/LakeCounty_PoliticalBoundaries/FeatureServer/7/",
    "FL:snapshot": "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/Precinct_gdb/FeatureServer/0/",
    "DE:2012": "https://hosting.firstmap.delaware.gov/hosting/rest/services/Boundaries/SenRepElectDistSP_Archived/MapServer/0/",
    "OK:2012": "https://services.arcgis.com/3xOwF6p0r7IHIjfn/arcgis/rest/services/Voter_Precints/FeatureServer/0/",
    "MI:2014": "https://gisagocss.state.mi.us/arcgis/rest/services/OpenData/boundaries/MapServer/0/",
}
ZIPS = {
    "MT": "https://ftpgeoinfo.msl.mt.gov/Data/Spatial/MSDI/AdministrativeBoundaries/Historic/MontanaVotingPrecinctsSHP2021.zip",
    "MT:2024": "https://ftpgeoinfo.msl.mt.gov/Data/Spatial/MSDI/AdministrativeBoundaries/Historic/MontanaPrecinctSplitsSHP20240215.zip",
    "NC": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20220831.zip",
    "TX": "https://data.capitol.texas.gov/dataset/d04c72b9-16c4-4ab2-8c6d-c666d41e04b7/resource/8d1035a6-0aaf-495e-aef1-ede0b70deae4/download/precincts22g.zip",
    "WA": "https://www.sos.wa.gov/sites/default/files/2023-05/statewide_precincts_2022general_ok_consol.zip",
    "LA": "https://redist.legis.la.gov/2022_GeoUpdates/2022_Louisiana_Precincts_(12-25-2022).zip",
    "MA": "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/shapefiles/state/wardsprecincts2022_poly.zip",
    "NM": "https://gstore.unm.edu/apps/rgis/datasets/3b1de40a-b37e-4d35-abdd-fd40bbbbe39e/New_Mexico_Voting_Precinct_20240703.original.zip",
    "NH": "https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Administrative_and_Political_Boundaries/d-nhpolitdists/2022/NHPolitDists2022.zip",
    "WV": "https://data.wvgis.wvu.edu/pub/Clearinghouse/Boundaries/WV_Voting_Districts_Precincts/Archive/WV_Precinct_2022_103122_UTM83.zip",
    "LA:1999": "https://redist.legis.la.gov/2011_Files/Shapefiles/1999%20LA%20Precincts.zip",
    "LA:2000": "https://redist.legis.la.gov/2011_Files/Shapefiles/2000%20Census%20LA%20Precincts.zip",
    "LA:2003-03": "https://redist.legis.la.gov/2011_Files/Shapefiles/2003-3%20LA%20Precincts.zip",
    "LA:2003-11": "https://redist.legis.la.gov/2011_Files/Shapefiles/2003-11%20LA%20Precincts.zip",
    "LA:2004": "https://redist.legis.la.gov/2011_Files/Shapefiles/2004-12%20LA%20Precincts.zip",
    "LA:2005": "https://redist.legis.la.gov/2011_Files/Shapefiles/2005-12%20LA%20Precincts.zip",
    "LA:2006": "https://redist.legis.la.gov/2011_Files/Shapefiles/2006-12%20LA%20Precincts.zip",
    "LA:2007": "https://redist.legis.la.gov/2011_Files/Shapefiles/Louisiana%20Precinct%20Shapefile%20(2007).zip",
    "LA:2008": "https://redist.legis.la.gov/2011_Files/Shapefiles/Precinct%20updates2008.zip",
    "LA:2009": "https://redist.legis.la.gov/2011_Files/Shapefiles/Precinct%20updates2009.zip",
    "LA:2010": "https://redist.legis.la.gov/2011_Files/Shapefiles/Precinct%20updates2010_11.zip",
    "LA:2011": "https://redist.legis.la.gov/2011_Files/Shapefiles/2011%20Louisiana%20Precinct%20Shapefile.zip",
    "LA:2012": "https://redist.legis.la.gov/2011_Files/Shapefiles/2012%20Louisiana%20Precinct%20Shapefile.zip",
    "LA:2013": "https://redist.legis.la.gov/2011_Files/Shapefiles/2013%20Louisiana%20Precinct%20Shapefile.zip",
    "LA:2014": "https://redist.legis.la.gov/2011_Files/Shapefiles/2014_LouisianaPrecinctShapefile.ZIP",
    "LA:2015": "https://redist.legis.la.gov/2011_Files/Shapefiles/2015_LouisianaPrecinctShapefile.zip",
    "WA:2004": "https://www.sos.wa.gov/sites/default/files/2022-05/2004.zip",
    "WA:2005": "https://www.sos.wa.gov/sites/default/files/2022-05/2005.zip",
    "WA:2006": "https://www.sos.wa.gov/sites/default/files/2022-05/2006.zip",
    "WA:2007": "https://www.sos.wa.gov/sites/default/files/2022-05/2007.zip",
    "WA:2008": "https://www.sos.wa.gov/sites/default/files/2022-05/2008.zip",
    "WA:2009": "https://www.sos.wa.gov/sites/default/files/2022-05/2009.zip",
    "WA:2010": "https://www.sos.wa.gov/sites/default/files/2022-05/2010.zip",
    "WA:2012": "https://www.sos.wa.gov/sites/default/files/2022-05/2012.zip",
    "WA:2013": "https://www.sos.wa.gov/sites/default/files/2022-05/Statewide_Prec_2013.zip",
    "WA:2014": "https://www.sos.wa.gov/sites/default/files/2022-05/Statewide_Prec_2014.zip",
    "MN:1996": "https://www.gis.lcc.mn.gov/data/shape/vtd96.zip",
    "MN:1998": "https://www.gis.lcc.mn.gov/data/shape/elec98.zip",
    "MN:2000": "https://www.gis.lcc.mn.gov/data/shape/elec00.zip",
    "MN:2002": "https://www.gis.lcc.mn.gov/data/shape/elec02.zip",
    "MN:2004": "https://www.gis.lcc.mn.gov/data/shape/elec04.zip",
    "MN:2006": "https://www.gis.lcc.mn.gov/data/shape/elec06.zip",
    "MN:2008": "https://www.gis.lcc.mn.gov/data/shape/elec08.zip",
    "MN:2010": "https://www.gis.lcc.mn.gov/data/shape/elec2010.zip",
    "TX:2010": "https://data.capitol.texas.gov/dataset/6381bd0b-3ad4-4556-bb3f-6c579a60d00a/resource/8aa724af-f8ed-4f7d-abd6-ea1642e0d2cc/download/2010general.zip",
    "TX:2012": "https://data.capitol.texas.gov/dataset/6381bd0b-3ad4-4556-bb3f-6c579a60d00a/resource/ab222bdb-37b9-4f78-9071-b15f9c5828cf/download/2012general.zip",
    "TX:2014": "https://data.capitol.texas.gov/dataset/6381bd0b-3ad4-4556-bb3f-6c579a60d00a/resource/b983204f-37d6-47e3-830e-e772cc465900/download/2014general.zip",
    "NC:2006": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/Precincts2006Gen.zip",
    "NC:2012": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20120901.zip",
    "NC:2014": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20141016.zip",
    "NC:2016": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20161004.zip",
    "NC:2017": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20170519.zip",
    "NC:2020": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20201018.zip",
    "WV:2002": "https://data.wvgis.wvu.edu/pub/Clearinghouse/Boundaries/votingDistrictsWV_legislativeServices_2002_utm83_shp.zip",
    "SC:snapshot": "https://rfa.sc.gov/media/10668",
    "NC:2024": "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20240723.zip",
    "TX:2024": "https://data.capitol.texas.gov/dataset/d04c72b9-16c4-4ab2-8c6d-c666d41e04b7/resource/4572665a-8de5-461d-84b8-79f704b63530/download/precincts24g.zip",
    "WA:2024": "https://www.sos.wa.gov/sites/default/files/2024-12/Statewide_Precincts_2024General_Consol.zip",
    "LA:2024": "https://redist.legis.la.gov/2025%201RS/Shapefiles/2024%20Precinct%20Shapefiles%20(12-31-2024).zip",
    "WV:2024": "https://data.wvgis.wvu.edu/pub/Clearinghouse/Boundaries/WV_Voting_Districts_Precincts/WV_Precinct_2024_20240213_UTM83.zip",
    "GA:2024": "https://www.legis.ga.gov/api/document/docs/default-source/reapportionment-document-library/precincts/gaprec_2024-website-shapefile.zip?sfvrsn=7b38ca99_2",
    "WI:2024": "https://web.s3.wisc.edu/rml-gisdata/WI_MunicipalWards_Fall_2024.zip",
}
ZIPS |= {f"{state}:2024": ZIPS[state] for state in ("MA", "NM", "NH")} | {
    "MS:1990": "https://maris.mississippi.edu/MARISdata/Political/MS_VotingPrecincts_1990.zip",
    "MA:2012": "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/shapefiles/state/wardsprecincts_poly.zip",
    "NH:2012": "https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Administrative_and_Political_Boundaries/d-nhpolitdists/NHPolitDists.zip",
    "WI:2011": "https://web.s3.wisc.edu/rml-gisdata/WI_MunicipalWards_2011.zip",
}
FILES = {
    "OH:2025": [
        "https://vote.franklincountyohio.gov/getmedia/65bd6003-91e0-497a-b1ba-f07c0ba5195e/Voting_Precinct_dbf",
        "https://vote.franklincountyohio.gov/getmedia/33541603-81d5-4c69-8608-331117b8d62a/Voting_Precinct_prj",
        "https://vote.franklincountyohio.gov/getmedia/59989478-b4b8-4562-bef0-ad34d50829bd/Voting_Precinct_shp",
        "https://vote.franklincountyohio.gov/getmedia/59e6d59c-d860-4427-b796-ad1566cff87c/Voting_Precinct_shx",
    ],
    "OH:2023": [
        "https://vote.franklincountyohio.gov/getmedia/f2d859aa-f107-4ac7-8f78-916c16355365/Voting_Precinct_dbf",
        "https://vote.franklincountyohio.gov/getmedia/d728dea4-98b4-475a-9238-9a343239d64f/Voting_Precinct_prj",
        "https://vote.franklincountyohio.gov/getmedia/11000da7-3309-4a6a-8b21-23c4d8cccbea/Voting_Precinct_shp",
        "https://vote.franklincountyohio.gov/getmedia/2e0c38af-aa90-417e-aa21-8c2af73d531e/Voting_Precinct_shx",
    ],
    "OH:2022": [
        "https://vote.franklincountyohio.gov/getmedia/eb50b952-a7a0-493a-be72-a582fa53ca8f/Voting-Precincts-G22-dbf",
        "https://vote.franklincountyohio.gov/getmedia/e4e36bd1-fec6-466a-ba2c-aee4084049fb/Voting-Precincts-G22-prj",
        "https://vote.franklincountyohio.gov/getmedia/cb0ff76d-fd59-403b-8aa2-4547b6d9d10c/Voting-Precincts-G22-shp",
        "https://vote.franklincountyohio.gov/getmedia/209f8e91-7300-4ebc-acbc-1cdc0b34fa94/Voting-Precincts-G22-shx",
    ],
    "OH:2019": [
        "https://vote.franklincountyohio.gov/getmedia/88b16596-9bd7-43d2-a020-01a15f796fcf/Precincts-dbf",
        "https://vote.franklincountyohio.gov/getmedia/f75ff88c-e9f3-4ecc-b105-5233182abdcf/Precincts-prj",
        "https://vote.franklincountyohio.gov/getmedia/36edc828-77cc-445c-9c03-569cb5739412/Precincts-shp",
        "https://vote.franklincountyohio.gov/getmedia/e20a31eb-b778-402f-8095-74ffadcf2743/Precincts-shx",
    ],
    "OH:2017": [
        "https://vote.franklincountyohio.gov/getmedia/a701414b-e15f-41bc-878b-2198e3632408/VotingPrecinct-dbf",
        "https://vote.franklincountyohio.gov/getmedia/a0d7edbd-cafa-4de2-9408-b493bfdd32b1/VotingPrecinct-prj",
        "https://vote.franklincountyohio.gov/getmedia/443fcf31-2b62-4ef6-a934-82f1d94765ca/VotingPrecinct-shp",
        "https://vote.franklincountyohio.gov/getmedia/7161857a-fae8-4040-b6ba-bd43fc50154f/VotingPrecinct-shx",
    ],
    "OH:2015": [
        "https://vote.franklincountyohio.gov/getmedia/097917aa-2d1e-4329-93eb-f41c38d1ea29/Precincts_P15-dbf",
        "https://vote.franklincountyohio.gov/getmedia/a2185f75-d0c0-4480-b129-ac97695f5d46/Precincts_P15-prj",
        "https://vote.franklincountyohio.gov/getmedia/89309b90-5ef2-4457-9ed6-d6200e932230/Precincts_P15-shp",
        "https://vote.franklincountyohio.gov/getmedia/88c9027c-74e3-4db9-8610-8abd67e3e757/Precincts_P15-shx",
    ],
    "OH:2014": [
        "https://vote.franklincountyohio.gov/getmedia/798b8ef3-76bf-44bf-b971-6aec43824c8d/Precincts_14-dbf",
        "https://vote.franklincountyohio.gov/getmedia/b0afa367-f25d-4e54-8f52-7f52ac990a9c/Precincts_14-prj",
        "https://vote.franklincountyohio.gov/getmedia/184d61ff-e309-4753-a443-f4068b6a304f/Precincts_14-shp",
        "https://vote.franklincountyohio.gov/getmedia/ea6689d1-fed7-489f-aa34-fe3dba1bc07f/Precincts_14-shx",
    ],
    "OH:2013": [
        "https://vote.franklincountyohio.gov/getmedia/34e0af44-abcf-4c6e-9b90-d52726bbd195/Precincts_G13-dbf",
        "https://vote.franklincountyohio.gov/getmedia/5c63c29b-471b-49cd-ad5c-d7423dbf4db8/Precincts_G13-prj",
        "https://vote.franklincountyohio.gov/getmedia/c0dc7186-d0e9-40e2-bbf7-d4b51b04ae1b/Precincts_G13-shp",
        "https://vote.franklincountyohio.gov/getmedia/7a9fac46-eefb-4e14-81ca-bb608fda4d7e/Precincts_G13-shx",
    ],
    "OH:2012": [
        "https://vote.franklincountyohio.gov/getmedia/7ad3fdb7-406c-4e7d-9ba8-a0d8875d6c2b/Precincts_G12-dbf",
        "https://vote.franklincountyohio.gov/getmedia/6cf3fde6-3ee8-4069-83ae-8016174851e9/Precincts_G12-prj",
        "https://vote.franklincountyohio.gov/getmedia/40612805-f294-4235-9263-e1a2cc0084af/Precincts_G12-shp",
        "https://vote.franklincountyohio.gov/getmedia/0a0453f2-3fb4-42ed-a396-2622577cccea/Precincts_G12-shx",
    ],
    "OH:2011": [
        "https://vote.franklincountyohio.gov/getmedia/8bcec880-99a6-4c1f-ac5a-1d46278372ce/Precincts_G11-dbf",
        "https://vote.franklincountyohio.gov/getmedia/e6eeac19-2ec2-4b5e-b7db-5e224d5cc47e/Precincts_G11-prj",
        "https://vote.franklincountyohio.gov/getmedia/74ea4e3d-e0aa-4114-b9dd-5c2e8945a869/Precincts_G11-shp",
        "https://vote.franklincountyohio.gov/getmedia/e6e32d4c-f772-44da-b6b9-25e0ddd1e0ee/Precincts_G11-shx",
    ],
    "OH:2010": [
        "https://vote.franklincountyohio.gov/getmedia/c46a1b16-5c22-48ee-9932-487e46a6319c/Precincts_10-dbf",
        "https://vote.franklincountyohio.gov/getmedia/5fc06924-a7c5-4446-87ad-7bb299594a4f/Precincts_10-prj",
        "https://vote.franklincountyohio.gov/getmedia/37c759e6-9d73-429c-94f7-60a22a1b8d7b/Precincts_10-shp",
        "https://vote.franklincountyohio.gov/getmedia/23657b97-199a-4ef5-8de9-ef5138e5a076/Precincts_10-shx",
    ],
    "OH:2009": [
        "https://vote.franklincountyohio.gov/getmedia/22d95b14-8bce-4bf8-b575-7a9800ea872b/Precincts_P09-dbf",
        "https://vote.franklincountyohio.gov/getmedia/3985d815-1afd-4d1b-847a-cd36f7cce0a5/Precincts_P09-prj",
        "https://vote.franklincountyohio.gov/getmedia/c4855443-c67d-4ead-8092-f04b6ea6184a/Precincts_P09-shp",
        "https://vote.franklincountyohio.gov/getmedia/caaac57b-5aa1-4fd3-ae79-1c107a161c6d/Precincts_P09-shx",
    ],
    "OH:2008": [
        "https://vote.franklincountyohio.gov/getmedia/fd71c636-ce38-4d51-9572-664a34611dca/Precincts_08-dbf",
        "https://vote.franklincountyohio.gov/getmedia/68580412-31b5-42d5-9bbc-8b62ce212dff/Precincts_08-shp",
        "https://vote.franklincountyohio.gov/getmedia/dcd190e0-5271-49e9-bb81-9f1e67d2af09/Precincts_08-shx",
    ],
    "OH:2007": [
        "https://vote.franklincountyohio.gov/getmedia/e233a707-89bd-4003-b731-30a03cdda142/Precincts_G07-dbf",
        "https://vote.franklincountyohio.gov/getmedia/6856100e-88a1-4245-882c-1ba2c366c77f/Precincts_G07-prj",
        "https://vote.franklincountyohio.gov/getmedia/f066e549-964f-4cfc-97bd-769a4fdb6b44/Precincts_G07-shp",
        "https://vote.franklincountyohio.gov/getmedia/365f713a-387e-48a8-8e8e-6bc8fb77337a/Precincts_G07-shx",
    ],
    "OH:2006": [
        "https://vote.franklincountyohio.gov/getmedia/82289fcd-f7a0-465d-beac-c4997c9acb81/Precincts_G06-dbf",
        "https://vote.franklincountyohio.gov/getmedia/2eca4be0-e2b2-49c9-9902-88a82fdc764b/Precincts_G06-shp",
        "https://vote.franklincountyohio.gov/getmedia/df0621d0-925a-480d-ba6c-2546e2a46d66/Precincts_G06-shx",
    ],
}
PROJECTIONS = {"WA:2006": 4269, "MN:1996": 26915, "MN:2002": 26915, "OH:2006": 32123, "OH:2008": 3735, "WI:2011": 3857}
GEOJSONS = {
    "NM:2006": "https://gstore.unm.edu/apps/rgisarchive/datasets/edf218ed-22cc-4e1e-ad3e-255626bea630/nmprecincts_08.derived.geojson",
}


def pages(state: str) -> list[str]:
    layer = LAYERS[state]
    count = json.loads(raw.cached(SOURCE, layer + "?f=json").path.read_text())["maxRecordCount"]
    urls = []
    while True:
        urls.append(layer + ARCGIS.format(offset=len(urls) * count, count=count))
        try:
            page = json.loads(raw.cached(SOURCE, urls[-1]).path.read_text())
        except LookupError:
            return urls
        if len(page["features"]) < count and not page.get("properties", {}).get("exceededTransferLimit"):
            return urls


def fetch() -> None:
    for layer in LAYERS.values():
        count = json.loads(raw.fetch(SOURCE, layer + "?f=json", suffix=".json").path.read_text())["maxRecordCount"]
        offset = 0
        while True:
            page = json.loads(
                raw.fetch(SOURCE, layer + ARCGIS.format(offset=offset, count=count), suffix=".geojson").path.read_text()
            )
            if len(page["features"]) < count and not page.get("properties", {}).get("exceededTransferLimit"):
                break
            offset += count
    for url in ZIPS.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    for url in GEOJSONS.values():
        raw.fetch(SOURCE, url, suffix=".geojson")
    for urls in FILES.values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=".zip")


def features(state: str) -> Iterator[tuple[dict[str, object], str, int]]:
    if state in LAYERS:
        for url in pages(state):
            for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]:
                if feature["geometry"] is not None:
                    yield feature["properties"], json.dumps(feature["geometry"]), 4326
        return
    contents: dict[str, bytes] = {}
    for url in FILES.get(state) or [GEOJSONS.get(state) or ZIPS[state]]:
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            contents |= {
                name.lower(): archive.read(name)
                for name in archive.namelist()
                if not name.startswith("__MACOSX") and not name.endswith("/")
            }
    for name in sorted(name for name in contents if name.endswith(".geojson")):
        for feature in json.loads(contents[name])["features"]:
            if feature["geometry"] is not None:
                yield feature["properties"], json.dumps(feature["geometry"]), 4326
    for stem in sorted(name[:-4] for name in contents if name.endswith(".shp")):
        reader = shapefile.Reader(
            shp=io.BytesIO(contents[f"{stem}.shp"]),
            shx=io.BytesIO(contents[f"{stem}.shx"]),
            dbf=io.BytesIO(contents[f"{stem}.dbf"]),
            encoding="latin-1",
        )
        crs = CRS(contents[f"{stem}.prj"].decode("latin-1")) if f"{stem}.prj" in contents else None
        srid = PROJECTIONS.get(state) or (crs.to_epsg(min_confidence=20) if crs is not None else None)
        assert srid is not None, f"{state}: {stem}"
        fields = [field[0] for field in reader.fields[1:]]
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            if record is None or shape is None or shape.shapeType == shapefile.NULL or not shape.points:
                continue
            try:
                geometry = shape.__geo_interface__
            except shapefile.RingSamplingError:
                rings = [
                    shape.points[start:end] for start, end in zip(shape.parts, [*shape.parts[1:], len(shape.points)])
                ]
                geometry = {"type": "Polygon", "coordinates": rings}
            yield dict(zip(fields, list(record), strict=True)), json.dumps(geometry), srid


def cased(text: str) -> str:
    return re.sub(r"\w+", lambda word: word[0] if re.search(r"\d", word[0]) else word[0].title(), text)


def canon(text: object) -> str:
    spaced = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", str(text).upper())
    return " ".join(
        (token.lstrip("0") or "0") if token.isdigit() else token for token in re.findall(r"[A-Z0-9]+", spaced)
    )


def bare(text: str) -> str:
    return canon(re.sub(r"^\s*(PRECINCT|PCT)\b", "", text, flags=re.IGNORECASE))


def town(text: str) -> str:
    match = re.fullmatch(r"(.+)_ward(\w+)_precinct(\w+)", text)
    return f"{canon(match[1])} W{canon(match[2])} P{canon(match[3])}" if match else canon(text)


def montana(text: str) -> str:
    return canon(re.sub(r"^(?:PRECINCT|PREC|PCT)[\s_#.]*0*", "", text.upper().strip()))


SHAPES: dict[str, Callable[[dict[str, Any]], tuple[str, str, str]]] = {
    "MN": lambda p: (f"27{p['countyfips']}", canon(p["pctname"]), p["pctname"]),
    "MI": lambda p: (f"26{p['COUNTYFIPS']}", canon(p["Precinct_Long_Name"]), p["Precinct_Long_Name"]),
    "MD": lambda p: (p["COUNTY"], canon(p["LABEL"]), f"Precinct {p['LABEL']}"),
    "DC": lambda p: ("11001", bare(p["NAME"]), p["NAME"]),
    "OK": lambda p: (f"40{p['CO_FIPS']}", canon(p["PCT_CEB"]), f"Precinct {p['PCT_CEB']}"),
    "RI": lambda p: ("", canon(p["DISTRICT"]), f"{p['TOWN']} {p['DISTRICT']}"),
    "MT": lambda p: (p["COUNTY"], montana(p["NAME"]), p["NAME"]),
    "IA": lambda p: (
        f"19{p['CoFIPS']}",
        canon(re.sub(r"^\d+\s+", "", p["PctNameOfficial"])),
        re.sub(r"^\d+\s+", "", p["PctNameOfficial"]),
    ),
    "AZ": lambda p: ("04013", canon(p["BdVal"]), f"{p['BdVal']} {cased(p['BdName'])}"),
    "NC": lambda p: (p["county_nam"], canon(p["prec_id"]), cased(p["enr_desc"])),
    "TX": lambda p: (f"48{int(p['CNTY']):03d}", canon(p["PREC"]), f"Precinct {p['PREC']}"),
    "WA": lambda p: (p["County"], canon(p["PrecName"]), p["PrecName"]),
    "LA": lambda p: (f"22{p['COUNTY']}", bare(p["NAME20"]), f"Precinct {p['NAME20']}"),
    "MA": lambda p: ("", town(f"{p['TOWN']}_ward{p['WARD'] or 0}_precinct{p['PRECINCT']}"), p["WP_NAME"]),
    "NM": lambda p: (f"35{p['COUNTY_FIP']}", canon(p["VTD_NUM"]), f"Precinct {p['VTD_NUM']}"),
    "WV": lambda p: (p["StCoFIPS"], canon(p["SHORTNAME"]), f"Precinct {p['SHORTNAME']}"),
    "NH": lambda p: ("", canon(re.sub(r"-\s*entire$", "", p["DISTRICT"])), re.sub(r"-\s*entire$", "", p["DISTRICT"])),
    "WI": lambda p: (p["CNTY_FIPS"], canon(p["RU"]), cased(p["RU"])),
}
UNITS: dict[str, Callable[[str, str], str]] = {
    "DC": lambda _, precinct: bare(precinct),
    "RI": lambda _, precinct: canon(precinct.split()[-1]),
    "AZ": lambda _, precinct: canon(precinct.split()[0]),
    "TX": lambda _, precinct: canon(precinct[3:7]),
    "MT": lambda _, precinct: montana(precinct),
    "LA": lambda _, precinct: re.sub(r"^0 ", "", canon(precinct)),
    "MA": lambda _, precinct: town(precinct),
    "NM": lambda _, precinct: bare(precinct),
    "WV": lambda _, precinct: bare(precinct),
}
STATEWIDE = ("RI", "MA", "NH")
FIPS = {
    "AK": "02",
    "AR": "05",
    "AZ": "04",
    "IA": "19",
    "MT": "30",
    "DC": "11",
    "GA": "13",
    "HI": "15",
    "IN": "18",
    "KY": "21",
    "LA": "22",
    "MA": "25",
    "MD": "24",
    "MI": "26",
    "MN": "27",
    "NC": "37",
    "ND": "38",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "OK": "40",
    "RI": "44",
    "SC": "45",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "WA": "53",
    "WV": "54",
    "OH": "39",
    "DE": "10",
    "MS": "28",
    "IL": "17",
    "FL": "12",
    "WI": "55",
}
GENERALS = [date(year, 11, 2 + (7 - date(year, 11, 1).weekday()) % 7) for year in range(1990, 2015, 2)]


def day(year: int) -> tuple[date, date]:
    general = date(year, 11, 2 + (7 - date(year, 11, 1).weekday()) % 7)
    return general, general + timedelta(days=1)


def years(first: int, last: int) -> tuple[date, date]:
    return date(first, 1, 1), date(min(last + 1, 2016), 1, 1)


def maricopa(p: dict[str, Any]) -> tuple[str, str, str]:
    number = int(p.get("BdVal") or p.get("BDVAL") or p.get("PctNum") or p.get("PCT_NUM") or 0)
    name = cased(str(p.get("BdName") or p.get("PctName") or p.get("PCT_NAME") or ""))
    return "04013", str(number), f"{number:04d} {name}".strip()


def lcc(p: dict[str, Any]) -> tuple[str, str, str]:
    if "DISTRICT" in p:
        label = next(p[field] for field in ("Precinct", "PRECINCT", "PRECINCTNA", "NAME_1") if field in p)
        return p["DISTRICT"][:5], canon(p["DISTRICT"][5:]), label
    if "CTYVTDCODE" in p:
        return f"27{p['CTYVTDCODE'][:3]}", canon(p["VTD"]), p["VTDNAME"]
    return f"27{p['COUNTY']}", canon(p["VTDCODE"][3:]), f"Precinct {p['VTD']}"


def census(p: dict[str, Any]) -> tuple[str, str, str]:
    geoid = next(field for field in p if field.startswith("GEOID"))
    suffix = geoid[5:]
    return p[geoid][:5], canon(p[f"VTDST{suffix}"]), p[f"NAME{suffix}"]


DC = ("District of Columbia Board of Elections", "CC BY 4.0", "DC GIS")
LCC = ("Minnesota Legislative Coordinating Commission, GIS Office", "Public record", None)
DATED: dict[str, tuple[date, date, str, str, str, str | None, Callable[[dict[str, Any]], tuple[str, str, str]]]] = {
    **{
        f"DC:{year}": (
            *years(int(year), end),
            DC[0],
            f"Voting Precinct - {year}",
            DC[1],
            DC[2],
            lambda p: ("11001", bare(p["NAME"]), p["NAME"]),
        )
        for year, end in (("1990", 2001), ("2002", 2007), ("2008", 2011), ("2012", 2018))
    },
    **{
        f"AZ:{era}": (
            *years(int(era[:4]), int(era[5:])),
            "Maricopa County Recorder",
            f"Maricopa County Voting Precincts ({era})",
            "Public record",
            None,
            maricopa,
        )
        for era in ("1998-1999", "2000-2001", "2002-2003", "2004-2005", "2006-2007", "2008-2011", "2012-2017")
    },
    **{
        f"MN:{year}": (
            *day(year),
            LCC[0],
            "1996 Precincts" if year == 1996 else f"{year} Election Results by Precinct",
            LCC[1],
            LCC[2],
            lcc,
        )
        for year in (1996, 1998, 2000, 2002, 2004, 2006, 2008, 2010)
    },
    **{
        f"MN:{year}": (
            *day(year),
            "Minnesota Secretary of State",
            f"General Election Results By Precinct {year}",
            "Public record",
            None,
            lambda p: (f"27{p['countyfips']}", canon(p["pctname"]), p["pctname"]),
        )
        for year in (2012, 2014)
    },
    **{
        f"LA:{vintage}": (
            *(
                (date(2003, 1, 1), date(2003, 11, 1))
                if vintage == "2003-03"
                else (date(2003, 11, 1), date(2004, 1, 1))
                if vintage == "2003-11"
                else years(int(vintage), int(vintage))
            ),
            "Louisiana Legislature",
            f"Louisiana Precinct Shapefile ({vintage})",
            "Public record",
            None,
            lambda p: (f"22{p['COUNTY']}", bare(p["VTD"]), p["UNIT_NAME"]),
        )
        for vintage in (
            "1999",
            "2000",
            "2003-03",
            "2003-11",
            *(str(year) for year in range(2004, 2016)),
        )
    },
    **{
        f"WA:{year}": (
            *years(year, year),
            "Washington Secretary of State",
            f"Statewide Precincts {year}",
            "Public record",
            None,
            census
            if year <= 2010
            else (lambda p: ("", f"{p['COUNTYCODE']} {canon(p['PRECCODE'])}", p["PRECNAME"]))
            if year == 2014
            else (lambda p: ("", f"{p['CountyCode']} {canon(p['Code'])}", p["Name"])),
        )
        for year in (2004, 2005, 2006, 2007, 2008, 2009, 2010, 2012, 2013, 2014)
    },
    **{
        f"TX:{year}": (
            *day(year),
            "Texas Legislative Council",
            f"Precincts, {year} General Election",
            "CC BY",
            None,
            lambda p: (f"48{int(p['CNTY']):03d}", canon(p["PREC"]), f"Precinct {p['PREC']}"),
        )
        for year in (2010, 2012, 2014)
    },
    "NC:2006": (
        *day(2006),
        "North Carolina State Board of Elections",
        "Precincts, 2006 General Election",
        "Public record",
        None,
        lambda p: (p["County"], canon(p["PRECINCT_I"]), p["Precinct"]),
    ),
    **{
        f"NC:{year}": (
            *day(year),
            "North Carolina State Board of Elections",
            f"Precinct Shapefile, {stamp}",
            "Public record",
            None,
            lambda p: (p["COUNTY_NAM"], canon(p["PREC_ID"]), p["ENR_DESC"].split("_", 1)[-1]),
        )
        for year, stamp in ((2012, "2012-09-01"), (2014, "2014-10-16"), (2016, "2016-10-04"))
    },
    "NC:2017": (
        date(2017, 5, 19),
        date(2019, 5, 7),
        "North Carolina State Board of Elections",
        "Precinct Shapefile, 2017-05-19",
        "Public record",
        None,
        lambda p: (p["COUNTY_NAM"], canon(p["PREC_ID"]), p["ENR_DESC"].split("_", 1)[-1]),
    ),
    "NC:2020": (
        *day(2020),
        "North Carolina State Board of Elections",
        "Precinct Shapefile, 2020-10-18",
        "Public record",
        None,
        lambda p: (p["county_nam"], canon(p["prec_id"]), p["enr_desc"]),
    ),
    "WV:2002": (
        *years(2002, 2002),
        "West Virginia GIS Technical Center",
        "Voting Districts, West Virginia Legislative Services, 2002",
        "Free to copy and distribute (MapWV terms of use)",
        None,
        lambda p: (p["COUNTY"], canon(p["NAME"]), f"Precinct {p['NAME']}"),
    ),
    "NM:2006": (
        date(2006, 7, 1),
        date(2012, 1, 1),
        "New Mexico Secretary of State",
        "Voting Precinct Boundaries as of July 2006",
        "Public domain",
        "RGIS",
        lambda p: (f"35{p['COUNTY']}", canon(p["VTD"]), p["NAME"].rstrip("*").strip()),
    ),
}
SNAPSHOTS: dict[
    str, tuple[date | None, date | None, str, str, str, str | None, Callable[[dict[str, Any]], tuple[str, str, str]]]
] = {
    "KY:snapshot": (
        None,
        None,
        "Kentucky State Board of Elections",
        "Voting Precinct Boundaries in Kentucky",
        "CC0 1.0",
        "Kentucky Division of Geographic Information",
        lambda p: (p["StCoFIPS_str"], canon(p["PrecinctCode"]), p["PrecinctName"]),
    ),
    "AR:snapshot": (
        None,
        None,
        "Arkansas Secretary of State",
        "Election Precincts",
        "Public record",
        "Arkansas GIS Office",
        lambda p: (f"05{p['county_fips']}", canon(p["precinct"]), p["precinct"]),
    ),
    "AK:snapshot": (
        None,
        None,
        "Alaska Division of Elections",
        "Alaska Election Precincts",
        "Public record",
        "Alaska Division of Community and Regional Affairs",
        lambda p: ("", p["Precinct_Name"].split()[0], p["Precinct_Name"]),
    ),
    "UT:snapshot": (
        None,
        None,
        "Utah Geospatial Resource Center",
        "Utah Vista Ballot Areas",
        "CC BY 4.0",
        "Utah AGRC, Utah Counties, Lt. Governor's Elections Office",
        lambda p: (f"49{2 * int(p['CountyID']) - 1:03d}", canon(p["PrecinctID"]), p["PrecinctID"]),
    ),
    "VT:snapshot": (
        None,
        None,
        "Vermont Center for Geographic Information",
        "Vermont Municipal Voting Districts",
        "Public record",
        None,
        lambda p: (
            "",
            f"{p['TOWNGEOID']} {canon(p['WARD'])}" if p["WARD"] else p["TOWNGEOID"],
            p["WARDUNI"] or p["TOWNREG"],
        ),
    ),
    "NJ:snapshot": (
        date(2021, 11, 24),
        date(2021, 11, 25),
        "New Jersey Department of State, Division of Elections",
        "Election Districts for New Jersey",
        "Public record",
        "NJGIN",
        lambda p: (
            p["COUNTY"],
            p["ELECD_KEY"],
            " ".join(
                [
                    p["MUN_NAME"],
                    *([f"Ward {p['WARD_CODE'].lstrip('0')}"] if p["WARD_CODE"] not in (None, "00") else []),
                    *([f"District {p['ELECD_CODE'].lstrip('0') or '0'}"] if p["ELECD_CODE"] else []),
                ]
            ),
        ),
    ),
    "ND:snapshot": (
        None,
        None,
        "North Dakota Secretary of State",
        "NDGISHUB Voter Precincts",
        "Public record",
        "NDGIS Hub",
        lambda p: (str(p["CountyID"]), p["Precinct"], f"Precinct {p['Precinct']}"),
    ),
    "SC:snapshot": (
        None,
        None,
        "South Carolina Revenue and Fiscal Affairs Office",
        "Statewide Precinct Shapefile",
        "Public record",
        None,
        lambda p: (f"45{int(p['FIPS']):03d}", canon(p["PCode"]), p["PName"]),
    ),
}
DATED |= {
    f"{state}:2024": (GENERAL, GENERAL + timedelta(days=1), *entry)
    for state, entry in {
        **{state: (*PUBLISHERS[state], SHAPES[state]) for state in ("DC", "OK", "RI", "AZ", "MA", "NM", "NH", "IA")},
        "NC": (
            "North Carolina State Board of Elections",
            "Precinct Shapefile, 2024-07-23",
            "Public record",
            None,
            SHAPES["NC"],
        ),
        "TX": ("Texas Legislative Council", "Precincts, 2024 General Election", "CC BY", None, SHAPES["TX"]),
        "WA": (
            "Washington Secretary of State",
            "Statewide Precincts, 2024 General, Consolidated",
            "Public record",
            None,
            lambda p: (p["County"], canon(str(p["PrecinctNu"])), p["PrecinctNa"]),
        ),
        "LA": (
            "Louisiana Legislature",
            "2024 Precinct Shapefiles (12-31-2024)",
            "Public record",
            None,
            lambda p: (f"22{p['COUNTYFP20']}", bare(p["NAME20"]), f"Precinct {p['NAME20']}"),
        ),
        "WV": (
            "West Virginia GIS Technical Center",
            "WV Precinct 2024 (2024-02-13)",
            "Free to copy and distribute (MapWV terms of use)",
            None,
            SHAPES["WV"],
        ),
        "GA": (
            "Georgia General Assembly, Legislative and Congressional Reapportionment Office",
            "2024 Precincts",
            "Public record",
            None,
            lambda p: (p["FIPS"], canon(p["PRECINCT_I"]), p["PRECINCT_N"]),
        ),
        "WI": (
            "Wisconsin Legislative Technology Services Bureau",
            "Municipal Wards, Fall 2024",
            "Public record",
            "LTSB, Wisconsin County Clerks and Land Information Offices",
            lambda p: (p["CNTY_FIPS"], f"{p['MCD_FIPS']} {canon(p['WARDID'])}", p["LABEL"]),
        ),
        "MN": (
            "Minnesota Secretary of State",
            "General Election Results By Precinct 2024",
            "Public record",
            None,
            lambda p: (f"27{p['countyfips']}", canon(p["pctcode"]), p["pctname"]),
        ),
        "MI": (
            "Michigan Department of State, Bureau of Elections",
            "2024 Voting Precincts",
            "Public record",
            None,
            SHAPES["MI"],
        ),
        "IN": (
            "Indiana Election Division",
            "Voting District Boundaries 2024",
            "Public record",
            "IndianaMap",
            lambda p: (f"18{p['county']}", canon(p["p24"]), f"Precinct {p['p24']}"),
        ),
        "HI": (
            "Hawaii State Office of Elections",
            "Election Precincts - 2024",
            "Public record",
            "Hawaii Statewide GIS Program",
            lambda p: ("", p["dp"], f"Precinct {p['dp']}"),
        ),
    }.items()
}
DATED |= {
    **{
        f"OH:{year}": (
            date(int(year), 1, 1),
            date(int(year) + 1, 1, 1),
            "Franklin County Board of Elections",
            f"GIS Shape Files, {year}",
            "Public record",
            None,
            lambda p: (
                "39049",
                str(p.get("PRECINCTID") or p.get("PRECCODE") or canon(p["NAME"])),
                f"{p['SUBDIV']} {p['WARD']}-{p['PRECINCT']}"
                if "SUBDIV" in p and "PRECINCT" in p
                else p.get("NAME") or p["Name"],
            ),
        )
        for year in (key.split(":")[1] for key in FILES)
    },
    "IN:2023": (
        date(2023, 11, 7),
        date(2023, 11, 8),
        "Indiana Election Division",
        "Voting District Boundaries 2023",
        "Public record",
        "IndianaMap",
        lambda p: (f"18{p['county']}", canon(p["p23"]), p["p23"]),
    ),
    **{
        f"IN:marion-{year}": (
            start,
            end,
            "Marion County Election Board",
            f"Voting Precincts {year}",
            "Public record",
            "Indianapolis and Marion County GIS",
            rule,
        )
        for year, start, end, rule in (
            ("1990", date(1990, 1, 1), date(2012, 1, 1), lambda p: ("18097", canon(p["PCT"]), p["PCT"])),
            ("2011", date(2012, 1, 1), date(2022, 1, 1), lambda p: ("18097", p["MCEB_PRECINCT"], p["MCEB_PRECINCT"])),
            ("2021", date(2022, 1, 1), date(2025, 1, 1), lambda p: ("18097", p["EBIRS_PRECINCT"], p["EBIRS_PRECINCT"])),
        )
    },
}
DATED |= {
    "MT:2024": (
        GENERAL,
        GENERAL + timedelta(days=1),
        "Montana State Library",
        "Montana Precinct Splits, 2024-02-15",
        "Public record",
        None,
        lambda p: (p["County"], montana(p["Prec_Name"]), p["Prec_Name"]),
    ),
    "RI:2016": (
        date(2016, 1, 1),
        date(2017, 1, 1),
        "Rhode Island Geographic Information System",
        "Voting Precincts (2016)",
        "Public record",
        "RIGIS",
        lambda p: ("", canon(p["NAME"].lstrip("0")), p["NAME"].lstrip("0")),
    ),
    "DE:2012": (
        *years(2012, 2015),
        "Delaware Department of Elections",
        "Election Districts_2012",
        "Public record",
        "Delaware FirstMap",
        lambda p: ("", p["EDRD_2012"], f"Election District {p['EDRD_2012']}"),
    ),
    "OK:2012": (
        *years(2012, 2015),
        "Oklahoma State Election Board",
        "Voter Precincts 2010",
        "Public record",
        "Center for Spatial Analysis, University of Oklahoma",
        lambda p: (f"40{p['GIS_COUNTY']}", canon(p["PCT_CEB"]), f"Precinct {p['PCT_CEB']}"),
    ),
    "MI:2014": (
        *day(2014),
        "Michigan Department of State, Bureau of Elections",
        "2014 Voting Precincts",
        "Public record",
        None,
        lambda p: (
            f"26{p['CountyFips']}",
            p["VP"],
            p["PrecinctLa"].strip()
            or " ".join([*([f"Ward {int(p['Ward'])}"] if p["Ward"] != "00" else []), f"Precinct {int(p['Precinct'])}"]),
        ),
    ),
    "MS:1990": (
        *years(1990, 1990),
        "Mississippi Automated Resource Information System",
        "Mississippi Voting Precincts 1990",
        "Public record",
        "MARIS",
        lambda p: (f"28{p['COUNTY']}", canon(p["PRECINCT"]), p["PRECINCT"]),
    ),
    "MA:2012": (*years(2012, 2015), "MassGIS", "2012 Wards and Precincts", "Public record", None, SHAPES["MA"]),
    "NH:2012": (
        *years(2012, 2015),
        "New Hampshire Office of Planning and Development",
        "NH Political Districts 2012",
        "Public record",
        "NH GRANIT",
        lambda p: (
            "",
            canon(re.sub(r"\s*-\s*entire$", "", p["WARD"], flags=re.IGNORECASE)),
            re.sub(r"\s*-\s*entire$", "", p["WARD"], flags=re.IGNORECASE),
        ),
    ),
    "WI:2011": (
        *years(2012, 2015),
        "Wisconsin Legislative Technology Services Bureau",
        "Municipal Wards 2011",
        "Public record",
        "LTSB, Wisconsin County Clerks and Land Information Offices",
        lambda p: (p["CNTY_FIPS"], p["WARD_FIPS"], p["NAME"]),
    ),
}
SNAPSHOTS |= {
    "IL:snapshot": (
        None,
        None,
        "Lake County Clerk",
        "Voting Precincts",
        "Public record",
        "Lake County, Illinois",
        lambda p: ("17097", str(p["PRECINCT"]), p["TWP_PCT"]),
    ),
    "FL:snapshot": (
        None,
        None,
        "Miami-Dade County Elections Department",
        "Precinct",
        "Public record",
        "Miami-Dade County",
        lambda p: ("12086", str(p["ID"]), f"Precinct {p['ID']}"),
    ),
}
WORDS = {"SAINT": "ST", "TOWNSHIP": "TWP", "HEIGHTS": "HTS", "MOUNT": "MT", "FORT": "FT"}
FILLER = {"CITY", "OF", "CHARTER", "PRECINCT", "PCT", "PREC", "P", "WARD", "W", "TOWN", "VILLAGE"}


def loose(key: str) -> str:
    return " ".join(WORDS.get(word, word) for word in key.split() if word not in FILLER)


def unordered(key: str) -> str:
    words = loose(key).split()
    return " ".join(
        [*sorted(word for word in words if not word.isdigit()), *(word for word in words if word.isdigit())]
    )


def group(rows: list[tuple[str, str, str, str, int]], prefix: str) -> pl.DataFrame:
    with connect() as conn:
        conn.execute(
            "create temp table shape (county text, key text, label text, geojson text, srid int) on commit drop"
        )
        with conn.cursor().copy("copy shape (county, key, label, geojson, srid) from stdin") as copy:
            for row in rows:
                copy.write_row(row)
        conn.execute(
            """create temp table grouped on commit drop as
               select county, key, min(label) as label, count(*) as parts,
                      st_multi(st_collectionextract(st_makevalid(st_simplifypreservetopology(st_union(
                        st_makevalid(st_transform(st_setsrid(st_geomfromgeojson(geojson), srid), 4326))), 0.00001)), 3)) as geom
               from shape group by county, key"""
        )
        conn.execute(
            """create temp table county on commit drop as
               select u.code, g.geom from reporting_unit u join area_geometry g on g.area_id = u.area_id
               where u.scheme_code = 'us.county' and u.code like %s""",
            [f"{prefix}%"],
        )
        conn.execute("create index on county using gist (geom)")
        return pl.DataFrame(
            conn.execute(
                """select g.county, g.key, g.label, g.parts,
                          (select c.code from county c order by c.geom <-> st_pointonsurface(g.geom) limit 1),
                          encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(st_pointonsurface(g.geom)), 'hex')
                   from grouped g where not st_isempty(g.geom)"""
            ).fetchall(),
            schema=["county", "key", "label", "parts", "within", "geom", "point"],
            orient="row",
        )


def michigan(precinct: str) -> str:
    match = re.fullmatch(r"(.*) (\S+) Ward (\S+)", precinct)
    if match is None:
        return canon(precinct)
    return canon(f"{match[1]} {match[2]}" if match[3] == "0" else f"{match[1]} {match[3]} {match[2]}")


UNITS24: dict[str, Callable[[str, str, str], str]] = {
    "TX": lambda county, _, precinct: canon(precinct.removeprefix(str((int(county[2:]) + 1) // 2))),
    "MI": lambda _, __, precinct: michigan(precinct),
}


def pair(units: pl.DataFrame, shapes: pl.DataFrame) -> pl.DataFrame:
    matched = pl.DataFrame(schema={"code": pl.String, "area": pl.String})
    for rule in (canon, lambda key: loose(canon(key)), lambda key: unordered(canon(key))):
        left = (
            units.join(matched, on="code", how="anti")
            .with_columns(match=pl.col("key").map_elements(rule, return_dtype=pl.String))
            .filter(pl.len().over("county", "match") == 1)
        )
        right = (
            shapes.join(matched, on="area", how="anti")
            .with_columns(match=pl.col("key").map_elements(rule, return_dtype=pl.String))
            .filter(pl.len().over("county", "match") == 1)
        )
        matched = pl.concat([matched, left.join(right, on=["county", "match"]).select("code", "area")])
    return matched


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        named = {
            (code[:2], text.upper()): code
            for code, text in conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.kind = 'short' where a.scheme_code = 'us.county'"""
            ).fetchall()
        }
    for vintage, (_, _, _, _, _, _, rule) in {**DATED, **SNAPSHOTS}.items():
        state = vintage.split(":")[0]
        rows = []
        for properties, geojson, srid in features(vintage):
            county, key, label = rule(properties)
            rows.append((named.get((FIPS[state], county.upper()), county), key, str(label).strip(), geojson, srid))
        if vintage == "WI:2024":
            wards = {}
            for county, key, label, _, _ in rows:
                match = re.fullmatch(r"(.+) - ([CTV]) \S+", label)
                if match is not None:
                    wards.setdefault((county, canon(match[1]), match[2], key.split(" ", 1)[1]), set()).add(key)
            groups: dict[str, list[str]] = {}
            for county, precinct in (
                pl.read_parquet(DATA / "parsed" / "us.medsl_precincts" / "units.parquet")
                .filter(pl.col("code").str.starts_with("2024:WI:"))
                .select("county", precinct=pl.col("code").str.split(":").list.get(4))
                .iter_rows()
            ):
                match = re.fullmatch(
                    r"(TOWN|CITY|VILLAGE) OF (.+?) WARDS? ([0-9A-Z]+(?:\s*[-,&]\s*[0-9A-Z]+)*)", precinct
                )
                if match is None:
                    continue
                numbers = []
                for item in re.split(r"\s*[,&]\s*", match[3]):
                    span = re.fullmatch(r"(\d+)\s*-\s*(\d+)", item)
                    if span is not None and int(span[1]) < int(span[2]):
                        numbers += [str(number) for number in range(int(span[1]), int(span[2]) + 1)]
                    elif re.fullmatch(r"\d+[A-Z]?", item):
                        numbers.append(canon(item))
                    else:
                        numbers = []
                        break
                found = [wards.get((county, canon(match[2]), match[1][0], number), set()) for number in numbers]
                if numbers and all(len(keys) == 1 for keys in found):
                    groups[precinct] = [key for keys in found for key in keys]
            claims = Counter(key for keys in groups.values() for key in keys)
            claimed = {
                key: precinct
                for precinct, keys in groups.items()
                if all(claims[key] == 1 for key in keys)
                for key in keys
            }
            rows += [
                (county, canon(claimed[key]), cased(claimed[key]), geojson, srid)
                for county, key, _, geojson, srid in rows
                if key in claimed
            ]
        shapes = group(rows, FIPS[state]).with_columns(
            county=pl.when(pl.col("county") == "").then("within").otherwise("county")
        )
        print(f"{vintage}: {len(rows)} features, {shapes.height} precincts", flush=True)
        shapes.write_parquet(PARSED / f"{vintage.replace(':', '_')}.parquet")

    vest = pl.read_parquet(DATA / "parsed" / "us.vest" / "precincts_2020.parquet").filter(pl.col("state") == "IA")
    caucuses = (
        pl.read_parquet(DATA / "parsed" / "us.idp" / "precincts.parquet")
        .select("county", "precinct")
        .unique()
        .with_columns(
            county=pl.col("county").map_elements(
                lambda name: named.get(("19", name.upper()), ""), return_dtype=pl.String
            )
        )
        .filter(pl.col("county") != "")
    )
    caucus = (
        pair(
            caucuses.select(
                code=pl.concat_str("county", pl.lit("\x1f"), "precinct"),
                county="county",
                key=pl.col("precinct").map_elements(html.unescape, return_dtype=pl.String),
            ),
            vest.select(area=pl.col("n").cast(pl.String), county="county", key="name"),
        )
        .select(
            county=pl.col("code").str.split("\x1f").list.get(0),
            key=pl.col("code").str.split("\x1f").list.get(1),
            n=pl.col("area").cast(pl.Int64),
        )
        .join(vest.select("n", label="name", geom="geom", point="point"), on="n")
        .with_columns(within="county")
    )
    print(f"IA:caucus: {caucus.height} of {caucuses.height} caucus precincts matched to {vest.height} VEST precincts")
    caucus.write_parquet(PARSED / "IA_caucus.parquet")

    units = pl.read_parquet(DATA / "parsed" / "us.medsl_precincts" / "units.parquet").with_columns(
        jurisdiction=pl.col("code").str.split(":").list.get(3),
        precinct=pl.col("code").str.split(":").list.slice(4).list.join(":"),
    )
    report = []
    for state, shape in SHAPES.items():
        rows = []
        for properties, geojson, srid in features(state):
            county, key, label = shape(properties)
            rows.append((named.get((FIPS[state], county.upper()), county), key, str(label).strip(), geojson, srid))
        shapes = group(rows, FIPS[state])
        unit = UNITS.get(state, lambda _, precinct: canon(precinct))
        mine = units.filter(pl.col("state") == state, pl.col("code").str.starts_with("2022:")).with_columns(
            county=pl.lit("") if state in STATEWIDE else pl.col("county"),
            key=pl.struct("jurisdiction", "precinct").map_elements(
                lambda row, unit=unit: unit(row["jurisdiction"], row["precinct"]), return_dtype=pl.String
            ),
        )
        passes = [
            pl.col("key"),
            pl.col("key").map_elements(loose, return_dtype=pl.String),
            pl.col("key").map_elements(unordered, return_dtype=pl.String),
        ]
        shapes = shapes.with_columns(code=pl.lit(None, pl.String))
        for number, rule in enumerate(passes):
            left = mine.filter(~pl.col("code").is_in(shapes["code"].drop_nulls().to_list())).with_columns(match=rule)
            if state == "LA" and number == 1:
                left = left.with_columns(
                    match=pl.col("precinct").map_elements(
                        lambda text: canon(text.split(" ", 1)[-1]), return_dtype=pl.String
                    )
                )
            found = (
                shapes.filter(pl.col("code").is_null())
                .with_columns(match=passes[0] if state == "LA" and number == 1 else rule)
                .filter(pl.len().over("county", "match") == 1)
                .join(
                    left.filter(pl.len().over("county", "match") == 1).select("county", "match", "code"),
                    on=["county", "match"],
                )
                .select("key", "county", pl.col("code_right").alias("found"))
            )
            shapes = (
                shapes.join(found, on=["county", "key"], how="left")
                .with_columns(code=pl.coalesce("code", "found"))
                .drop("found")
            )
        matched = shapes
        report.append(
            (
                state,
                mine.height,
                shapes.height,
                matched["code"].is_not_null().sum(),
                shapes.filter(pl.col("parts") > 1).height,
            )
        )
        print(
            f"{state}: {report[-1][3]} of {mine.height} units matched to {shapes.height} shapes "
            f"({report[-1][4]} keys with several features)",
            flush=True,
        )
        matched.with_columns(
            state=pl.lit(state), county=pl.when(pl.col("county") == "").then("within").otherwise("county")
        ).write_parquet(PARSED / f"{state}_2022.parquet")
    pl.DataFrame(report, schema=["state", "units", "shapes", "matched", "merged"], orient="row").write_parquet(
        PARSED / "report_2022.parquet"
    )

    units = (
        pl.concat(
            [
                pl.read_parquet(DATA / "parsed" / pipeline / "units.parquet").select("code", "state", "county")
                for pipeline in ("us.medsl_precincts", "us.medsl_state_precincts")
            ]
        )
        .unique("code")
        .with_columns(
            year=pl.col("code").str.slice(0, 4),
            jurisdiction=pl.col("code").str.split(":").list.get(3),
            precinct=pl.col("code").str.split(":").list.slice(4).list.join(":"),
        )
    )
    known = set(named.values())
    counties = {code: name for (_, name), code in named.items()}
    jurisdictions: dict[tuple[str, str], set[str]] = {}
    for properties, _, _ in features("MI"):
        jurisdictions.setdefault(
            (
                f"26{properties['COUNTYFIPS']}",
                canon(
                    re.sub(
                        r"^(CITY|TOWNSHIP) OF (.+)$",
                        r"\2 \1",
                        str(properties["Precinct_Long_Name"]).split(",")[0].upper().replace("CHARTER ", ""),
                    )
                ),
            ),
            set(),
        ).add(str(properties["MCDFIPS"]))
    mcds = {key: next(iter(codes)) for key, codes in jurisdictions.items() if len(codes) == 1}
    links = []
    for year in ("2016", "2018", "2020"):
        vest = pl.read_parquet(DATA / "parsed" / "us.vest" / f"precincts_{year}.parquet").select(
            area=pl.format("{}:{}:{}", pl.lit(year), "state", "n"),
            county="county",
            name=pl.col("name").str.replace(r"\s+\d{3}-\d{2}$", ""),
            bare=pl.struct("county", "name").map_elements(
                lambda row: re.sub(
                    rf"^({re.escape(counties.get(row['county'], '~'))}|Voting District)\s+",
                    "",
                    re.sub(
                        r" - ([TCV]) 0*(\d+)$|\s+(?:Borough|Township|City|Town|Village)$"
                        if row["county"][:2] == "34"
                        else r" - ([TCV]) 0*(\d+)$",
                        lambda hit: f" - {hit[1]} {hit[2]}" if hit[1] else "",
                        row["name"],
                    ),
                    flags=re.IGNORECASE,
                ),
                return_dtype=pl.String,
            ),
        )
        mine = units.filter(pl.col("year") == year).with_columns(
            clean=pl.when(pl.col("state") == "TX")
            .then(pl.col("precinct").str.slice(3, 4))
            .when(pl.col("state") == "CO")
            .then(pl.col("precinct").str.slice(-3))
            .when(pl.col("state") == "WV")
            .then(pl.col("precinct").str.replace(r"^PRECINCT\s+", ""))
            .when(pl.col("state") == "NJ")
            .then(pl.col("precinct").str.replace(r"\s+(?:TWP|TOWNSHIP|BORO|BOROUGH|CITY|TOWN|VILLAGE)$", ""))
            .when(pl.col("state") == "CT")
            .then(
                pl.struct("jurisdiction", "precinct").map_elements(
                    lambda row: (
                        ""
                        if (match := re.match(r"DISTRICT (\d+)-(?:(\d+)-)?", row["precinct"] or "")) is None
                        else f"{row['jurisdiction']} {int(match[1]):03d}-{int(match[2] or 0):02d}"
                    ),
                    return_dtype=pl.String,
                )
            )
            .when(pl.col("state") == "RI")
            .then(
                pl.struct("jurisdiction", "precinct").map_elements(
                    lambda row: (
                        f"{row['jurisdiction']} {int(match[1]) % 100}"
                        if (match := re.search(r"(\d+)$", row["precinct"] or "")) is not None
                        else ""
                    ),
                    return_dtype=pl.String,
                )
            )
            .when(pl.col("state") == "WI")
            .then(
                pl.col("precinct")
                .str.to_uppercase()
                .str.replace(r"^(T|C|V)(?:OWN|ITY|ILLAGE) OF (.+) WARD (\d+)$", "${2} - ${1} ${3}")
            )
            .otherwise(
                pl.col("precinct").map_elements(
                    lambda text: re.sub(r"^(NAN_)?(\d+\s*-?\s*(?=[A-Za-z]))?", "", text), return_dtype=pl.String
                )
            )
        )
        wards: dict[tuple[str, str, str, str], list[str]] = {}
        for county, name, geom in (
            pl.read_parquet(DATA / "parsed" / "us.vest" / f"precincts_{year}.parquet")
            .filter(pl.col("state") == "WI")
            .select("county", "name", "geom")
            .iter_rows()
        ):
            match = re.fullmatch(r"(.+) - ([CTV]) 0*(\d+[A-Z]?)", name)
            if match is not None:
                wards.setdefault((county, canon(match[1]), match[2], canon(match[3])), []).append(geom)
        groups = {}
        for code, county, precinct in (
            mine.filter(pl.col("state") == "WI")
            .select("code", "county", pl.col("precinct").str.to_uppercase())
            .iter_rows()
        ):
            match = re.fullmatch(r"(TOWN|CITY|VILLAGE) OF (.+?) WARDS? ([0-9A-Z]+(?:\s*[-,&]\s*[0-9A-Z]+)*)", precinct)
            if match is None:
                continue
            numbers = []
            for item in re.split(r"\s*[,&]\s*", match[3]):
                span = re.fullmatch(r"(\d+)\s*-\s*(\d+)", item)
                if span is not None and int(span[1]) < int(span[2]):
                    numbers += [str(number) for number in range(int(span[1]), int(span[2]) + 1)]
                elif re.fullmatch(r"\d+[A-Z]?", item):
                    numbers.append(canon(item))
                else:
                    numbers = []
                    break
            found = [wards.get((county, canon(match[2]), match[1][0], number), []) for number in numbers]
            if len(numbers) > 1 and all(len(geoms) == 1 for geoms in found):
                groups[code] = (county, precinct, [geoms[0] for geoms in found])
        claims = Counter(geom for _, _, geoms in groups.values() for geom in geoms)
        groups = {code: entry for code, entry in groups.items() if all(claims[geom] == 1 for geom in entry[2])}
        with connect() as conn:
            conn.execute("create temp table ward (code text, geom text)")
            with conn.cursor().copy("copy ward (code, geom) from stdin") as copy:
                for code, (_, _, geoms) in groups.items():
                    for geom in geoms:
                        copy.write_row((code, geom))
            dissolved = pl.DataFrame(
                conn.execute(
                    """select code, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                       from (select code, st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(geom::geometry))), 3))
                             as geom from ward group by code) dissolved
                       where not st_isempty(geom)""",
                ).fetchall(),
                schema={"unit": pl.String, "geom": pl.String, "point": pl.String},
                orient="row",
            ).with_columns(
                county=pl.col("unit").replace_strict({code: entry[0] for code, entry in groups.items()}),
                key=pl.col("unit").replace_strict({code: canon(entry[1]) for code, entry in groups.items()}),
                label=pl.col("unit").replace_strict({code: cased(entry[1]) for code, entry in groups.items()}),
            )
        dissolved.drop("unit").with_columns(within="county").write_parquet(PARSED / f"WI_vest{year}.parquet")
        wisconsin = dissolved.select(
            code="unit", area=pl.format("{}:WI:{}:{}", pl.lit(str(day(int(year))[0])), "county", "key")
        )
        print(f"{year} WI: {wisconsin.height} ward groups dissolved from VEST's wards", flush=True)
        vintage = {"2016": "NC_2016", "2018": "NC_2017", "2020": "NC_2020"}[year]
        sbe = pl.read_parquet(PARSED / f"{vintage}.parquet")
        start_date = str(DATED[vintage.replace("_", ":")][0])
        found = pair(
            mine.filter(pl.col("state") == "NC").select("code", "county", key="precinct"),
            sbe.select(area=pl.format("{}:NC:{}:{}", pl.lit(start_date), "county", "key"), county="county", key="key"),
        )
        found = pl.concat(
            [
                found,
                pair(
                    mine.filter(pl.col("state") == "NC")
                    .join(found, on="code", how="anti")
                    .select("code", "county", key="precinct"),
                    sbe.filter(
                        ~pl.format("{}:NC:{}:{}", pl.lit(start_date), "county", "key").is_in(found["area"].to_list())
                    ).select(
                        area=pl.format("{}:NC:{}:{}", pl.lit(start_date), "county", "key"), county="county", key="label"
                    ),
                ),
            ]
        )
        print(f"{year} NC: {found.height} units matched to the State Board's precincts", flush=True)
        if year == "2016":
            found = pl.concat(
                [
                    found,
                    pair(
                        mine.filter(pl.col("state") == "RI", ~pl.col("jurisdiction").str.ends_with(" LI")).select(
                            "code",
                            county=pl.lit("RI"),
                            key=pl.col("precinct").str.extract(r"(\d+)$").str.strip_chars_start("0"),
                        ),
                        pl.read_parquet(PARSED / "RI_2016.parquet")
                        .filter(pl.col("key") != "")
                        .select(
                            area=pl.format(
                                "2016-01-01:RI:{}:{}",
                                pl.when(pl.col("county") == "").then("within").otherwise("county"),
                                "key",
                            ),
                            county=pl.lit("RI"),
                            key="key",
                        ),
                    ),
                ]
            )
        rows = []
        for code, county, jurisdiction, precinct in (
            mine.filter(pl.col("state") == "MI")
            .join(found, on="code", how="anti")
            .select("code", "county", "jurisdiction", "precinct")
            .iter_rows()
        ):
            name, text = precinct.split("; ", 1) if "; " in precinct else (jurisdiction, precinct)
            match = re.fullmatch(r"Ward (\d+); Precinct (\d+)|(\d+)(?:, WARD (\d+))?", text)
            mcd = mcds.get((county, canon(name.upper().replace("CHARTER ", ""))))
            if match is not None and mcd is not None:
                rows.append((code, county, f"{county[2:]}{mcd}", match[1] or match[4] or "", match[2] or match[3]))
        michigan_units = pl.DataFrame(rows, schema=["code", "county", "place", "ward", "number"], orient="row")
        michigan_vest = vest.filter(pl.col("county").str.starts_with("26"), pl.col("name").str.contains(r"^\d{13}$"))
        found = pl.concat(
            [
                found,
                pair(
                    michigan_units.select(
                        "code",
                        "county",
                        key=pl.format(
                            "{}{}{}",
                            "place",
                            pl.col("ward").str.zfill(2),
                            pl.col("number").str.zfill(3),
                        ),
                    ),
                    michigan_vest.select("area", "county", key="name"),
                ),
            ]
        )
        found = pl.concat(
            [
                found,
                pair(
                    michigan_units.filter(pl.col("ward") == "")
                    .join(found, on="code", how="anti")
                    .select("code", "county", key=pl.format("{}~{}", "place", pl.col("number").str.zfill(3))),
                    michigan_vest.join(found, on="area", how="anti").select(
                        "area",
                        "county",
                        key=pl.format("{}~{}", pl.col("name").str.slice(0, 8), pl.col("name").str.slice(10, 3)),
                    ),
                ),
            ]
        )
        for unit, shape in (("precinct", "bare"), ("clean", "bare"), ("clean", "name"), ("jurisdiction", "name")):
            found = pl.concat(
                [
                    found,
                    pair(
                        mine.join(found, on="code", how="anti").select("code", "county", key=unit),
                        vest.join(found, on="area", how="anti").select("area", "county", key=shape),
                    ),
                ]
            )
        found = pl.concat([found, wisconsin.join(found, on="code", how="anti")])
        links.append(found)
        for state, total, matched in (
            mine.join(found, on="code", how="left")
            .group_by("state")
            .agg(pl.len(), pl.col("area").is_not_null().sum())
            .sort("state")
            .iter_rows()
        ):
            print(f"{year} {state}: {matched} of {total} units matched to official or VEST shapes", flush=True)
    shapes = pl.concat(
        [
            pl.read_parquet(PARSED / f"{vintage.replace(':', '_')}.parquet")
            .filter(pl.col("key") != "")
            .with_columns(
                state=pl.lit(vintage[:2]),
                county=pl.when(pl.col("county").is_in(list(known))).then("county").otherwise("within"),
            )
            .select(
                area=pl.format("{}:{}:{}:{}", pl.lit(str(GENERAL)), "state", "county", "key"),
                county=pl.when(pl.col("state").is_in(STATEWIDE)).then("state").otherwise("county"),
                key="key",
                alt=pl.col("label").str.replace(r"^\d+\s+", ""),
            )
            for vintage in DATED
            if vintage.endswith(":2024")
        ]
    )
    mine = units.filter(
        pl.col("year") == "2024", pl.col("state").is_in([vintage[:2] for vintage in DATED if vintage.endswith(":2024")])
    ).with_columns(
        key=pl.struct("state", "county", "jurisdiction", "precinct").map_elements(
            lambda row: (
                UNITS24[row["state"]](row["county"], row["jurisdiction"], row["precinct"])
                if row["state"] in UNITS24
                else UNITS.get(row["state"], lambda _, precinct: canon(precinct))(row["jurisdiction"], row["precinct"])
            ),
            return_dtype=pl.String,
        ),
        alt=pl.col("precinct").str.replace(r"^\d+\s+", ""),
        county=pl.when(pl.col("state").is_in(STATEWIDE)).then("state").otherwise("county"),
    )
    found = pair(mine.select("code", "county", "key"), shapes.select("area", "county", "key"))
    found = pl.concat(
        [
            found,
            pair(
                mine.join(found, on="code", how="anti").select("code", "county", key="alt"),
                shapes.join(found, on="area", how="anti").select("area", "county", key="alt"),
            ),
        ]
    )
    links.append(found)
    for state, total, matched in (
        mine.join(found, on="code", how="left")
        .group_by("state")
        .agg(pl.len(), pl.col("area").is_not_null().sum())
        .sort("state")
        .iter_rows()
    ):
        print(f"2024 {state}: {matched} of {total} units matched to official shapes", flush=True)
    pl.concat(links).write_parquet(PARSED / "links.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Official precinct shapes from state and county election offices, 1990–2015, 2022, and current "
            "snapshots, with 2022's joined to MEDSL's precincts and VEST's 2020 shapes carried to Iowa's caucus precincts",
            polity="US",
        )
        counties = {
            code: (area, text.rsplit(",", 1)[0])
            for code, area, text in conn.execute(
                """select c.code, a.id, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
                   where a.scheme_code = 'us.county'"""
            ).fetchall()
        }
        existing = pl.DataFrame(
            conn.execute(
                """select n.id::text, n.entity_id::text, n.text from name n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.kind = 'official' and n.lang = 'en'""",
                [SOURCE],
            ).fetchall(),
            schema={"id": pl.String, "entity_id": pl.String, "old": pl.String},
            orient="row",
        )
        retrieved = {
            vintage: raw.cached(
                SOURCE, LAYERS[vintage] + "?f=json" if vintage in LAYERS else ZIPS[vintage]
            ).retrieved_at.date()
            for vintage, entry in SNAPSHOTS.items()
            if entry[0] is None
        }
        for general in [*GENERALS, CAUCUS, DAY, GENERAL, *sorted(set(retrieved.values()))]:
            cs.add(
                "boundary_set",
                scheme_code="us.precinct",
                vintage=str(general.year) if general in (*GENERALS, DAY, GENERAL) else str(general),
                valid_from=general,
                valid_to=general + timedelta(days=1),
                as_of=general,
            )
        vintages = {
            **{state: (DAY, DAY + timedelta(days=1), *PUBLISHERS[state]) for state in SHAPES},
            **{vintage: entry[:6] for vintage, entry in DATED.items()},
            **{
                vintage: (
                    start or retrieved[vintage],
                    end or retrieved[vintage] + timedelta(days=1),
                    publisher,
                    title,
                    license,
                    attribution,
                )
                for vintage, (start, end, publisher, title, license, attribution, _) in SNAPSHOTS.items()
            },
            **{
                f"WI:vest{year}": (
                    *day(year),
                    "Voting and Election Science Team",
                    f"{year} Precinct-Level Election Results",
                    "CC BY 4.0",
                    None,
                )
                for year in (2016, 2018, 2020)
            },
            "IA:caucus": (
                CAUCUS,
                CAUCUS + timedelta(days=1),
                "Voting and Election Science Team",
                "2020 Precinct-Level Election Results",
                "CC BY 4.0",
                None,
            ),
        }
        staged: set[UUID] = set()
        for vintage, (start, end, publisher, title, license, attribution) in vintages.items():
            state = vintage.split(":")[0]
            source = cs.source(publisher, title)
            if vintage != "IA:caucus" and "vest" not in vintage and source not in staged:
                staged.add(source)
                url = (
                    LAYERS.get(vintage)
                    or ZIPS.get(vintage)
                    or GEOJSONS.get(vintage)
                    or "https://vote.franklincountyohio.gov/maps-and-data/gis-shape-files"
                )
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license=license,
                    attribution=attribution,
                )
                for cached in [url + "?f=json", *pages(vintage)] if vintage in LAYERS else FILES.get(vintage) or [url]:
                    cs.artifact(raw.cached(SOURCE, cached), source)
            elif source in cs.minted and source not in staged:
                raise ValueError(f"{title} must already be a source")
            frame = (
                pl.read_parquet(PARSED / f"{vintage.replace(':', '_')}{'' if ':' in vintage else '_2022'}.parquet")
                .filter(pl.col("key") != "", ~pl.col("county").str.contains(r"^\d{2}(000)?$"))
                .with_columns(
                    known=pl.col("county").is_in(list(counties)),
                    label=pl.when(pl.col("label") == "").then("key").otherwise("label"),
                )
                .sort("known", descending=True, maintain_order=True)
                .with_columns(county=pl.when("known").then("county").otherwise("within"))
                .with_columns(
                    code=pl.coalesce("code", pl.format("2022:{}:{}:#{}", pl.lit(state), "county", "key"))
                    if ":" not in vintage
                    else pl.format("{}:{}:{}:{}", pl.lit(str(start)), pl.lit(state), "county", "key")
                )
                .unique("code", keep="first", maintain_order=True)
            )
            ids = [str(cs.area("us.precinct", code)) for code in frame["code"]]
            cs.frame("area", pl.DataFrame({"id": ids, "scheme_code": "us.precinct"}))
            cs.frame(
                "area_code",
                pl.DataFrame({"area_id": ids, "code": frame["code"]}).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_parent",
                pl.DataFrame(
                    {"area_id": ids, "parent_id": [str(counties[county][0]) for county in frame["county"]]}
                ).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_geometry",
                pl.DataFrame({"area_id": ids, "geom": frame["geom"], "label_point": frame["point"]}).with_columns(
                    valid_from=pl.lit(start),
                    valid_to=pl.lit(end),
                    quality=pl.lit("academic" if vintage == "IA:caucus" or "vest" in vintage else "official"),
                    source_id=pl.lit(str(source)),
                ),
            )
            cs.frame(
                "name",
                pl.DataFrame(
                    {
                        "entity_id": ids,
                        "text": [
                            f"{f'Precinct {label}' if label.isdigit() else cased(label) if label.isupper() else label}, "
                            f"{counties[county][1]}"
                            for label, county in frame.select("label", "county").iter_rows()
                        ],
                    }
                )
                .join(existing, on="entity_id", how="left")
                .sort(pl.col("old") == pl.col("text"), descending=True, nulls_last=True)
                .with_columns(first=pl.int_range(pl.len()).over("entity_id") == 0)
                .select(
                    "id",
                    "entity_id",
                    text=pl.when("first").then("text").otherwise("old"),
                    is_preferred="first",
                    lang=pl.lit("en"),
                    kind=pl.lit("official"),
                ),
            )
            print(f"{vintage}: {frame.height} precincts, {start} to {end}")
        return cs.write()
