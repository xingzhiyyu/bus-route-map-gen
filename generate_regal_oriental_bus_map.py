#!/usr/bin/env python3
import argparse
import base64
import html
import json
import math
import os
import time
import urllib.parse
import urllib.request


WORKDIR = os.path.dirname(os.path.abspath(__file__))
BUS_STOPS_URL = "https://static.data.gov.hk/td/routes-fares-geojson/JSON_BUS.json"
CSDI_ROUTE_URL = "https://portal.csdi.gov.hk/server/rest/services/common/td_rcd_1638844988873_41214/FeatureServer/0/query"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson"
TILE_URL = "https://a.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png"

BUS_STOPS_FILE = os.path.join(WORKDIR, "JSON_BUS.json")
ROUTE_LINES_FILE = os.path.join(WORKDIR, "regal_oriental_route_lines.geojson")
OSM_ROADS_FILE = os.path.join(WORKDIR, "regal_oriental_osm_roads.json")
OSM_WATER_FILE = os.path.join(WORKDIR, "regal_oriental_osm_water.json")
LAND_FILE = os.path.join(WORKDIR, "ne_10m_land.geojson")
TILE_DIR = os.path.join(WORKDIR, "tiles_cartodb_light_nolabels")

WIDTH = 3200
HEIGHT = 2200
LEGEND_W = 610
PADDING = 90
TARGET_STOP_KEYWORDS = ("富豪東方", "富豪东方", "REGAL ORIENTAL")
BASEMAP_ZOOM = 12
DRAW_VECTOR_CONTEXT = False


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a Hong Kong bus route map for all routes passing a named stop."
    )
    parser.add_argument(
        "--stop",
        default="富豪東方",
        help="Stop-name keyword to match in Chinese or English stop names. Default: 富豪東方",
    )
    return parser.parse_args()


def filename_slug(text):
    allowed = []
    for ch in text.strip():
        if ch.isalnum() or ch in ("-", "_"):
            allowed.append(ch)
        elif ch.isspace():
            allowed.append("_")
    slug = "".join(allowed).strip("_")
    return slug or "stop"


def output_paths(stop_keyword):
    slug = filename_slug(stop_keyword)
    return {
        "route_lines": os.path.join(WORKDIR, f"bus_map_{slug}_route_lines.geojson"),
        "svg": os.path.join(WORKDIR, f"bus_map_{slug}.svg"),
        "html": os.path.join(WORKDIR, f"bus_map_{slug}.html"),
        "lines_only": os.path.join(WORKDIR, f"bus_map_{slug}_lines_only.svg"),
    }


def get_json(url, params=None, post_data=None, timeout=90):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        data=post_data,
        headers={
            "User-Agent": "Mozilla/5.0 bus-map-generator/1.0",
            "Accept": "application/json,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
    return json.loads(raw.decode("utf-8-sig"))


def ensure_file(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 bus-map-generator/1.0"})
    with urllib.request.urlopen(req, timeout=120) as res, open(path, "wb") as f:
        f.write(res.read())


def normalize_color_key(route_name, all_routes):
    route_name = route_name.upper()
    if route_name.startswith("N") and len(route_name) > 1:
        base = route_name[1:]
        if base in all_routes:
            return base
    return route_name


def geo_distance_m(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = (lon2 - lon1) * 111320 * math.cos(mean_lat)
    dy = (lat2 - lat1) * 110540
    return math.hypot(dx, dy)


def clean_stop_name(name):
    return str(name).replace("<br>", " / ").replace("\n", " ").strip()


def unique_target_stops(target_hits):
    stops = {}
    for feature in target_hits:
        p = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"]
        key = (int(p["stopId"]), round(lon, 6), round(lat, 6), clean_stop_name(p.get("stopNameS", "")))
        stops[key] = stops.get(key, 0) + 1
    return sorted(
        [
            {
                "stopId": stop_id,
                "lon": lon,
                "lat": lat,
                "name": name,
                "records": records,
            }
            for (stop_id, lon, lat, name), records in stops.items()
        ],
        key=lambda s: (s["name"], s["stopId"], s["lon"], s["lat"]),
    )


def validate_target_stops(stop_keyword, target_hits, max_spread_m=900):
    stops = unique_target_stops(target_hits)
    max_dist = 0
    for i, a in enumerate(stops):
        for b in stops[i + 1:]:
            max_dist = max(max_dist, geo_distance_m((a["lon"], a["lat"]), (b["lon"], b["lat"])))
    if len(stops) > 1 and max_dist > max_spread_m:
        print(f'Stop keyword "{stop_keyword}" matched multiple far-apart stops ({len(stops)} unique stop records):')
        for s in stops:
            print(f'  stopId={s["stopId"]}  {s["name"]}  ({s["lon"]}, {s["lat"]})  records={s["records"]}')
        raise SystemExit(
            "The stop keyword is too broad. Use a more specific stop name, e.g. include the terminal/platform/place suffix."
        )
    return stops


def color_for_index(i, total):
    # Golden-angle hues, tuned away from pale yellows for legibility.
    hue = (i * 137.508 + 8) % 360
    sat = 72
    light = 43 if total > 50 else 45
    return hsl_to_hex(hue, sat / 100, light / 100)


def hsl_to_hex(h, s, l):
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return "#{:02x}{:02x}{:02x}".format(round((r + m) * 255), round((g + m) * 255), round((b + m) * 255))


def mercator(lon, lat):
    lat = max(min(lat, 85.05112878), -85.05112878)
    x = math.radians(lon)
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def lonlat_to_tile(lon, lat, zoom):
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(max(min(lat, 85.05112878), -85.05112878))
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_bounds(x, y, zoom):
    n = 2 ** zoom
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0

    def tile_y_to_lat(ty):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))

    north = tile_y_to_lat(y)
    south = tile_y_to_lat(y + 1)
    return west, south, east, north


def tile_data_uri(x, y, zoom):
    os.makedirs(TILE_DIR, exist_ok=True)
    path = os.path.join(TILE_DIR, str(zoom), str(x), f"{y}.png")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        url = TILE_URL.format(z=zoom, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 bus-map-generator/1.0"})
        with urllib.request.urlopen(req, timeout=60) as res, open(path, "wb") as f:
            f.write(res.read())
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")


class Projector:
    def __init__(self, bounds):
        west, south, east, north = bounds
        x0, y0 = mercator(west, south)
        x1, y1 = mercator(east, north)
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        map_w = WIDTH - LEGEND_W - PADDING * 2
        map_h = HEIGHT - PADDING * 2
        self.scale = min(map_w / (x1 - x0), map_h / (y1 - y0))
        self.off_x = PADDING + (map_w - (x1 - x0) * self.scale) / 2
        self.off_y = PADDING + (map_h - (y1 - y0) * self.scale) / 2

    def xy(self, lon, lat):
        x, y = mercator(lon, lat)
        return self.off_x + (x - self.x0) * self.scale, HEIGHT - (self.off_y + (y - self.y0) * self.scale)

    def lonlat(self, x, y):
        mx = (x - self.off_x) / self.scale + self.x0
        my = ((HEIGHT - y) - self.off_y) / self.scale + self.y0
        lon = math.degrees(mx)
        lat = math.degrees(2 * math.atan(math.exp(my)) - math.pi / 2)
        return lon, lat

    def visible_bounds(self, x0, y0, x1, y1):
        a = self.lonlat(x0, y1)
        b = self.lonlat(x1, y0)
        return a[0], a[1], b[0], b[1]


def simplify_xy(points, tolerance):
    if tolerance <= 0 or len(points) < 3:
        return points

    def dist_to_segment(p, a, b):
        px, py = p
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        qx, qy = ax + t * dx, ay + t * dy
        return math.hypot(px - qx, py - qy)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        max_dist = -1
        max_idx = None
        for i in range(start + 1, end):
            dist = dist_to_segment(points[i], points[start], points[end])
            if dist > max_dist:
                max_dist = dist
                max_idx = i
        if max_idx is not None and max_dist > tolerance:
            keep[max_idx] = True
            stack.append((start, max_idx))
            stack.append((max_idx, end))
    return [pt for pt, should_keep in zip(points, keep) if should_keep]


def point_text(points, proj, tolerance=0):
    if len(points) < 2:
        return ""
    xy_points = [proj.xy(lon, lat) for lon, lat in points]
    xy_points = simplify_xy(xy_points, tolerance)
    parts = []
    for i, (x, y) in enumerate(xy_points):
        parts.append(("M" if i == 0 else "L") + f"{x:.1f},{y:.1f}")
    return " ".join(parts)


def geom_lines(geometry):
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "LineString":
        return [coords]
    if gtype == "MultiLineString":
        return coords
    return []


def geom_polygons(geometry):
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        return coords
    if gtype == "MultiPolygon":
        return [ring for poly in coords for ring in poly]
    return []


def bbox_intersects(bounds, ring):
    west, south, east, north = bounds
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return not (max(xs) < west or min(xs) > east or max(ys) < south or min(ys) > north)


def clip_polygon_to_bbox(points, bounds):
    west, south, east, north = bounds

    def clip_edge(poly, inside, intersect):
        if not poly:
            return []
        out = []
        prev = poly[-1]
        prev_inside = inside(prev)
        for cur in poly:
            cur_inside = inside(cur)
            if cur_inside:
                if not prev_inside:
                    out.append(intersect(prev, cur))
                out.append(cur)
            elif prev_inside:
                out.append(intersect(prev, cur))
            prev, prev_inside = cur, cur_inside
        return out

    def ix_x(x):
        def fn(a, b):
            ax, ay = a
            bx, by = b
            t = 0 if bx == ax else (x - ax) / (bx - ax)
            return [x, ay + (by - ay) * t]
        return fn

    def ix_y(y):
        def fn(a, b):
            ax, ay = a
            bx, by = b
            t = 0 if by == ay else (y - ay) / (by - ay)
            return [ax + (bx - ax) * t, y]
        return fn

    poly = points
    poly = clip_edge(poly, lambda p: p[0] >= west, ix_x(west))
    poly = clip_edge(poly, lambda p: p[0] <= east, ix_x(east))
    poly = clip_edge(poly, lambda p: p[1] >= south, ix_y(south))
    poly = clip_edge(poly, lambda p: p[1] <= north, ix_y(north))
    if len(poly) > 2 and poly[0] != poly[-1]:
        poly.append(poly[0])
    return poly


def route_sort_key(name):
    text = name.upper().lstrip("N")
    num = ""
    rest = ""
    for ch in text:
        if ch.isdigit() and not rest:
            num += ch
        else:
            rest += ch
    return (int(num) if num else 9999, rest, name)


def find_target_routes(bus_data, stop_keyword):
    keyword = stop_keyword.upper()
    hits = []
    route_ids = set()
    route_keys = set()
    target_stop_ids = set()
    for feature in bus_data["features"]:
        p = feature["properties"]
        names = " ".join(str(p.get(k, "")) for k in ("stopNameC", "stopNameS", "stopNameE")).upper()
        if keyword in names:
            hits.append(feature)
            route_ids.add(int(p["routeId"]))
            route_keys.add((int(p["routeId"]), int(p["routeSeq"])))
            target_stop_ids.add(int(p["stopId"]))
    return hits, sorted(route_ids), route_keys, target_stop_ids


def collect_stops(bus_data, route_keys):
    stops = []
    route_names = {}
    for feature in bus_data["features"]:
        p = feature["properties"]
        route_key = (int(p["routeId"]), int(p["routeSeq"]))
        if route_key not in route_keys:
            continue
        lon, lat = feature["geometry"]["coordinates"]
        route_name = str(p["routeNameE"])
        route_names[int(p["routeId"])] = route_name
        stops.append(
            {
                "routeId": int(p["routeId"]),
                "routeSeq": int(p["routeSeq"]),
                "stopSeq": int(p["stopSeq"]),
                "stopId": int(p["stopId"]),
                "route": route_name,
                "company": p.get("companyCode", ""),
                "lon": lon,
                "lat": lat,
                "nameC": p.get("stopNameC", ""),
                "nameS": p.get("stopNameS", ""),
                "nameE": p.get("stopNameE", ""),
            }
        )
    return stops, route_names


def filter_route_lines(route_lines, route_keys):
    return {
        "type": "FeatureCollection",
        "features": [
            f for f in route_lines.get("features", [])
            if (int(f["properties"].get("ROUTE_ID")), int(f["properties"].get("ROUTE_SEQ"))) in route_keys
        ],
    }


def fetch_route_lines(route_ids, route_lines_file, force=False):
    if os.path.exists(route_lines_file) and not force:
        cached = json.load(open(route_lines_file, encoding="utf-8"))
        cached_ids = {int(f["properties"].get("ROUTE_ID")) for f in cached.get("features", [])}
        if cached_ids == set(route_ids):
            return cached
        print("Cached route geometry is for a different stop; refetching.")
    if route_lines_file != ROUTE_LINES_FILE and os.path.exists(ROUTE_LINES_FILE) and not force:
        cached = json.load(open(ROUTE_LINES_FILE, encoding="utf-8"))
        cached_ids = {int(f["properties"].get("ROUTE_ID")) for f in cached.get("features", [])}
        if cached_ids == set(route_ids):
            with open(route_lines_file, "w", encoding="utf-8") as f:
                json.dump(cached, f, ensure_ascii=False)
            return cached
    features = []
    for idx, route_id in enumerate(route_ids, 1):
        params = {
            "where": f"ROUTE_ID={route_id}",
            "outFields": "ROUTE_ID,ROUTE_SEQ,COMPANY_CODE,ROUTE_NAMEE,ST_STOP_ID,ED_STOP_ID",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        }
        data = get_json(CSDI_ROUTE_URL, params=params, timeout=120)
        features.extend(data.get("features", []))
        if idx % 20 == 0:
            print(f"Fetched {idx}/{len(route_ids)} route geometries")
        time.sleep(0.08)
    out = {"type": "FeatureCollection", "features": features}
    with open(route_lines_file, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out


def bounds_from(lines, stops):
    lons, lats = [], []
    for feature in lines["features"]:
        for line in geom_lines(feature["geometry"]):
            for lon, lat in line:
                lons.append(lon)
                lats.append(lat)
    for s in stops:
        lons.append(s["lon"])
        lats.append(s["lat"])
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    dx = (east - west) * 0.035
    dy = (north - south) * 0.055
    return west - dx, south - dy, east + dx, north + dy


def fetch_osm_roads(bounds, force=False):
    if os.path.exists(OSM_ROADS_FILE) and not force:
        return json.load(open(OSM_ROADS_FILE, encoding="utf-8"))
    west, south, east, north = bounds
    query = f"""
[out:json][timeout:90];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"]({south},{west},{north},{east});
);
out body geom;
"""
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error = None
    for url in OVERPASS_URLS:
        try:
            data = get_json(url, post_data=payload, timeout=140)
            with open(OSM_ROADS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
        except Exception as exc:
            last_error = exc
            print(f"Overpass failed via {url}: {exc}")
    print(f"OSM road fetch skipped: {last_error}")
    return {"elements": []}


def fetch_osm_water(bounds, force=False):
    if os.path.exists(OSM_WATER_FILE) and not force:
        return json.load(open(OSM_WATER_FILE, encoding="utf-8"))
    west, south, east, north = bounds
    query = f"""
[out:json][timeout:90];
(
  way["natural"="coastline"]({south},{west},{north},{east});
  way["natural"="water"]({south},{west},{north},{east});
  way["waterway"~"^(river|canal)$"]({south},{west},{north},{east});
);
out body geom;
"""
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error = None
    for url in OVERPASS_URLS:
        try:
            data = get_json(url, post_data=payload, timeout=140)
            with open(OSM_WATER_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
        except Exception as exc:
            last_error = exc
            print(f"Overpass water failed via {url}: {exc}")
    print(f"OSM water fetch skipped: {last_error}")
    return {"elements": []}


def fetch_land(force=False):
    if os.path.exists(LAND_FILE) and not force:
        return json.load(open(LAND_FILE, encoding="utf-8"))
    ensure_file(LAND_URL, LAND_FILE)
    return json.load(open(LAND_FILE, encoding="utf-8"))


def render_svg(bus_data, route_lines, roads, water, land, stops, route_names, target_stop_ids, target_hits, stop_keyword):
    all_route_names = {v.upper() for v in route_names.values()}
    color_keys = sorted({normalize_color_key(name, all_route_names) for name in all_route_names}, key=route_sort_key)
    colors = {key: color_for_index(i, len(color_keys)) for i, key in enumerate(color_keys)}
    route_color = {}
    for route_id, name in route_names.items():
        route_color[route_id] = colors[normalize_color_key(name, all_route_names)]

    bounds = bounds_from(route_lines, stops)
    proj = Projector(bounds)
    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">')
    svg.append(f'<title>经过{html.escape(stop_keyword)}的公交线路图</title>')
    svg.append("<style>")
    svg.append(
        "text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans CJK SC','Microsoft YaHei',Arial,sans-serif}"
        ".small{font-size:24px;fill:#53606b}.label{font-size:29px;font-weight:700;fill:#15191d}"
        ".legend{font-size:22px;fill:#20252b}.routeLabel{font-size:18px;font-weight:700;fill:#fff}"
    )
    svg.append("</style>")
    svg.append('<rect width="100%" height="100%" fill="#f4f1eb"/>')
    map_w = WIDTH - LEGEND_W
    svg.append(f'<rect x="0" y="0" width="{map_w}" height="{HEIGHT}" fill="#d8edf4"/>')
    svg.append("<defs>")
    svg.append(f'<clipPath id="mapClip"><rect x="0" y="0" width="{map_w}" height="{HEIGHT}"/></clipPath>')
    svg.append("</defs>")

    tile_bounds_visible = proj.visible_bounds(0, 0, map_w, HEIGHT)
    west, south, east, north = tile_bounds_visible
    x0, y0 = lonlat_to_tile(west, north, BASEMAP_ZOOM)
    x1, y1 = lonlat_to_tile(east, south, BASEMAP_ZOOM)
    svg.append('<g clip-path="url(#mapClip)">')
    for txi in range(x0, x1 + 1):
        for tyi in range(y0, y1 + 1):
            tw, ts, te, tn = tile_bounds(txi, tyi, BASEMAP_ZOOM)
            px0, py0 = proj.xy(tw, tn)
            px1, py1 = proj.xy(te, ts)
            href = tile_data_uri(txi, tyi, BASEMAP_ZOOM)
            svg.append(
                f'<image href="{href}" x="{px0:.1f}" y="{py0:.1f}" '
                f'width="{px1 - px0 + 0.8:.1f}" height="{py1 - py0 + 0.8:.1f}" '
                'preserveAspectRatio="none" opacity="0.92"/>'
            )
    svg.append("</g>")

    if DRAW_VECTOR_CONTEXT:
        land_bounds = proj.visible_bounds(0, 0, map_w, HEIGHT)
        land_paths = []
        for feature in land.get("features", []):
            for ring in geom_polygons(feature.get("geometry", {})):
                if not ring or not bbox_intersects(land_bounds, ring):
                    continue
                clipped = clip_polygon_to_bbox(ring, land_bounds)
                if len(clipped) < 4:
                    continue
                d = point_text(clipped, proj, tolerance=1.0)
                if d:
                    land_paths.append(d + " Z")
        if land_paths:
            svg.append(f'<path d="{" ".join(land_paths)}" fill="#f7f4ed" stroke="none" opacity="1"/>')

        for elem in water.get("elements", []):
            tags = elem.get("tags", {})
            geom = elem.get("geometry", [])
            pts = [(p["lon"], p["lat"]) for p in geom if "lon" in p and "lat" in p]
            d = point_text(pts, proj, tolerance=0.8)
            if not d:
                continue
            if tags.get("natural") == "water":
                closed = len(pts) > 3 and abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6
                svg.append(f'<path d="{d}{" Z" if closed else ""}" fill="#d8edf4" stroke="#94b7c5" stroke-width="1.2" opacity="0.72"/>')
            elif tags.get("natural") == "coastline":
                svg.append(f'<path d="{d}" fill="none" stroke="#7caabd" stroke-width="2.6" stroke-linecap="butt" stroke-linejoin="round" opacity="0.80"/>')
            else:
                svg.append(f'<path d="{d}" fill="none" stroke="#b5ccd5" stroke-width="2.0" stroke-linecap="butt" stroke-linejoin="round" opacity="0.65"/>')

        road_widths = {"motorway": 7.0, "trunk": 5.8, "primary": 4.7, "secondary": 3.6, "tertiary": 2.6}
        for elem in roads.get("elements", []):
            tags = elem.get("tags", {})
            highway = tags.get("highway", "")
            geom = elem.get("geometry", [])
            pts = [(p["lon"], p["lat"]) for p in geom if "lon" in p and "lat" in p]
            d = point_text(pts, proj, tolerance=1.8)
            if not d:
                continue
            width = road_widths.get(highway, 2.2)
            svg.append(f'<path d="{d}" fill="none" stroke="#d5d0c5" stroke-width="{width + 1.4:.1f}" stroke-linecap="butt" stroke-linejoin="round" opacity="0.70"/>')
            svg.append(f'<path d="{d}" fill="none" stroke="#fbf9f3" stroke-width="{width:.1f}" stroke-linecap="butt" stroke-linejoin="round" opacity="0.78"/>')

    # Draw muted route casings first.
    for feature in route_lines["features"]:
        rid = int(feature["properties"].get("ROUTE_ID"))
        for line in geom_lines(feature["geometry"]):
            d = point_text(line, proj, tolerance=1.2)
            if d:
                svg.append(f'<path d="{d}" fill="none" stroke="#ffffff" stroke-width="8.2" stroke-linecap="round" stroke-linejoin="round" opacity="0.62"/>')

    for feature in route_lines["features"]:
        p = feature["properties"]
        rid = int(p.get("ROUTE_ID"))
        color = route_color.get(rid, "#444")
        for line in geom_lines(feature["geometry"]):
            d = point_text(line, proj, tolerance=1.2)
            if d:
                svg.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" opacity="0.70"/>')

    # Stops are deduplicated by stop id and rounded coordinate so repeated route records don't create blobs.
    stop_seen = set()
    for s in stops:
        key = (s["stopId"], round(s["lon"], 6), round(s["lat"], 6))
        if key in stop_seen:
            continue
        stop_seen.add(key)
        x, y = proj.xy(s["lon"], s["lat"])
        if s["stopId"] in target_stop_ids:
            continue
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.1" fill="#1e252b" stroke="#ffffff" stroke-width="1.5" opacity="0.78"/>')

    target_seen = set()
    for hit in target_hits:
        p = hit["properties"]
        stop_id = int(p["stopId"])
        lon, lat = hit["geometry"]["coordinates"]
        key = (stop_id, round(lon, 6), round(lat, 6))
        if key in target_seen:
            continue
        target_seen.add(key)
        x, y = proj.xy(lon, lat)
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="16" fill="#f7c948" stroke="#111820" stroke-width="4"/>')
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="#111820"/>')

    # Legend panel.
    lx = WIDTH - LEGEND_W + 36
    svg.append(f'<rect x="{WIDTH - LEGEND_W}" y="0" width="{LEGEND_W}" height="{HEIGHT}" fill="#fbfaf6"/>')
    svg.append(f'<line x1="{WIDTH - LEGEND_W}" y1="0" x2="{WIDTH - LEGEND_W}" y2="{HEIGHT}" stroke="#d9d4ca" stroke-width="2"/>')
    safe_stop = html.escape(stop_keyword)
    svg.append(f'<text x="{lx}" y="68" font-size="38" font-weight="800" fill="#14181d">经过{safe_stop}的公交</text>')
    svg.append(f'<text class="small" x="{lx}" y="108">Official TD/CSDI bus data + OpenStreetMap roads</text>')
    svg.append(f'<text class="small" x="{lx}" y="140">同线路号及对应夜班线共用颜色</text>')
    route_list = sorted(all_route_names, key=route_sort_key)
    cols = 3 if len(route_list) > 72 else 2
    row_h = 35
    col_w = (LEGEND_W - 70) / cols
    start_y = 188
    for i, name in enumerate(route_list):
        col = i // math.ceil(len(route_list) / cols)
        row = i % math.ceil(len(route_list) / cols)
        x = lx + col * col_w
        y = start_y + row * row_h
        key = normalize_color_key(name, all_route_names)
        svg.append(f'<rect x="{x:.1f}" y="{y - 18:.1f}" width="28" height="8" rx="4" fill="{colors[key]}"/>')
        svg.append(f'<text class="legend" x="{x + 38:.1f}" y="{y - 10:.1f}">{html.escape(name)}</text>')

    footer_y = HEIGHT - 105
    svg.append(f'<text class="small" x="{lx}" y="{footer_y}">线路数量：{len(route_list)}；目标站点 ID：{", ".join(map(str, sorted(target_stop_ids)))}</text>')
    svg.append(f'<text class="small" x="{lx}" y="{footer_y + 32}">站点均不显示名称；目标站点以黄色同心圆标出。</text>')
    svg.append(f'<text class="small" x="{lx}" y="{footer_y + 64}">线路数据：DATA.GOV.HK/CSDI，底图道路：OpenStreetMap。</text>')
    svg.append("</svg>")
    return "\n".join(svg)


def render_lines_only_svg(route_lines, stops, route_names):
    all_route_names = {v.upper() for v in route_names.values()}
    color_keys = sorted({normalize_color_key(name, all_route_names) for name in all_route_names}, key=route_sort_key)
    colors = {key: color_for_index(i, len(color_keys)) for i, key in enumerate(color_keys)}
    route_color = {
        route_id: colors[normalize_color_key(name, all_route_names)]
        for route_id, name in route_names.items()
    }

    bounds = bounds_from(route_lines, stops)
    proj = Projector(bounds)
    map_w = WIDTH

    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{map_w}" height="{HEIGHT}" viewBox="0 0 {map_w} {HEIGHT}">')
    svg.append("<defs>")
    svg.append(f'<clipPath id="mapClip"><rect x="0" y="0" width="{map_w}" height="{HEIGHT}"/></clipPath>')
    svg.append("</defs>")
    svg.append('<g clip-path="url(#mapClip)">')

    for feature in route_lines["features"]:
        for line in geom_lines(feature["geometry"]):
            d = point_text(line, proj, tolerance=1.2)
            if d:
                svg.append(f'<path d="{d}" fill="none" stroke="#ffffff" stroke-width="8.2" stroke-linecap="round" stroke-linejoin="round" opacity="0.55"/>')

    for feature in route_lines["features"]:
        p = feature["properties"]
        rid = int(p.get("ROUTE_ID"))
        color = route_color.get(rid, "#444")
        for line in geom_lines(feature["geometry"]):
            d = point_text(line, proj, tolerance=1.2)
            if d:
                svg.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round" opacity="0.82"/>')

    svg.append("</g>")
    svg.append("</svg>")
    return "\n".join(svg)


def write_html(svg_text, output_html, stop_keyword):
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(
            f"<!doctype html><meta charset='utf-8'><title>经过{html.escape(stop_keyword)}的公交线路图</title>"
            "<style>body{margin:0;background:#e8e4da}svg{display:block;max-width:100%;height:auto;margin:auto}</style>"
            + svg_text
        )


def main():
    args = parse_args()
    paths = output_paths(args.stop)
    ensure_file(BUS_STOPS_URL, BUS_STOPS_FILE)
    bus_data = json.load(open(BUS_STOPS_FILE, encoding="utf-8-sig"))
    target_hits, route_ids, route_keys, target_stop_ids = find_target_routes(bus_data, args.stop)
    if not route_ids:
        raise SystemExit(f"No target routes found for stop keyword: {args.stop}")
    matched_stops = validate_target_stops(args.stop, target_hits)
    stops, route_names = collect_stops(bus_data, route_keys)
    print(f"Stop keyword: {args.stop}")
    print(f"Target stop hits: {len(target_hits)}")
    print(f"Unique matched stop records: {len(matched_stops)}")
    print(f"Route variants: {len(route_ids)}")
    print(f"Route directions: {len(route_keys)}")
    print(f"Route numbers: {len(set(route_names.values()))}")
    route_lines_all = fetch_route_lines(route_ids, paths["route_lines"])
    route_lines = filter_route_lines(route_lines_all, route_keys)
    bounds = bounds_from(route_lines, stops)
    roads = fetch_osm_roads(bounds) if DRAW_VECTOR_CONTEXT else {"elements": []}
    water = fetch_osm_water(bounds) if DRAW_VECTOR_CONTEXT else {"elements": []}
    land = fetch_land() if DRAW_VECTOR_CONTEXT else {"features": []}
    svg_text = render_svg(bus_data, route_lines, roads, water, land, stops, route_names, target_stop_ids, target_hits, args.stop)
    with open(paths["svg"], "w", encoding="utf-8") as f:
        f.write(svg_text)
    write_html(svg_text, paths["html"], args.stop)
    with open(paths["lines_only"], "w", encoding="utf-8") as f:
        f.write(render_lines_only_svg(route_lines, stops, route_names))
    print(f"Wrote {paths['svg']}")
    print(f"Wrote {paths['html']}")
    print(f"Wrote {paths['lines_only']}")
    print(f"OSM vector roads drawn: {len(roads.get('elements', []))}")
    print(f"OSM vector water/coastline ways drawn: {len(water.get('elements', []))}")


if __name__ == "__main__":
    main()
