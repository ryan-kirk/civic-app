# dallas_gis_parcel_lookup.py
from __future__ import annotations
import zipfile
import requests
import geopandas as gpd
from shapely.geometry import Point

PARCEL_ZIP_URL = "https://geodallas.dallascountyiowa.gov/SpatialDownload/ParcelShape.zip"
# (Shown on GeoDallas spatial download page listing ParcelShape.shp.) :contentReference[oaicite:4]{index=4}

def download_and_load_parcels(tmp_zip="ParcelShape.zip", extract_dir="parcelshape"):
    r = requests.get(PARCEL_ZIP_URL, timeout=60)
    r.raise_for_status()
    with open(tmp_zip, "wb") as f:
        f.write(r.content)

    with zipfile.ZipFile(tmp_zip, "r") as z:
        z.extractall(extract_dir)

    # Find .shp inside extract_dir (commonly ParcelShape.shp)
    # If exact name differs, list directory and adjust.
    shp_path = f"{extract_dir}/ParcelShape.shp"
    parcels = gpd.read_file(shp_path)
    return parcels

def parcel_for_point(parcels: gpd.GeoDataFrame, lon: float, lat: float):
    # Ensure CRS alignment
    pt = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326")
    if parcels.crs is None:
        raise ValueError("Parcels shapefile CRS unknown; check metadata.")
    pt_proj = pt.to_crs(parcels.crs)

    hits = parcels[parcels.geometry.contains(pt_proj.iloc[0])]
    return hits.iloc[0] if len(hits) else None

if __name__ == "__main__":
    parcels = download_and_load_parcels()

    # You need a lat/lon for the address.
    # In production you would geocode (Nominatim/Google/etc.) or use an internal address point dataset.
    lon, lat = -93.8, 41.67  # EXAMPLE ONLY

    hit = parcel_for_point(parcels, lon, lat)
    if hit is None:
        print("No parcel found at that point (or point outside Dallas County).")
    else:
        # Field names vary (e.g., PARCELNO, PIN, etc.). Inspect columns.
        print(hit)