import json

import shapely


def label_point(geojson: str) -> str:
    point = shapely.from_geojson(json.dumps(json.loads(geojson)), on_invalid="ignore").representative_point()
    return shapely.to_wkb(shapely.set_srid(point, 4326), hex=True, include_srid=True)
