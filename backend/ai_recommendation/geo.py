import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two GPS points in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def eta_minutes(distance_km, speed_kmh=40.0, road_factor=1.3):
    """Rough ETA: straight-line distance scaled by a road-winding factor.
    Replace with Google Directions / OSRM for real routing."""
    return round((distance_km * road_factor) / speed_kmh * 60, 1)
