# HK Bus Stop Map

Generate bus route maps for Hong Kong stops using official Transport Department/CSDI bus data and an OpenStreetMap-derived basemap.

## Command line

```bash
python3 generate_regal_oriental_bus_map.py --stop "机场(地面运输中心)"
```

The script writes:

- `bus_map_<站名>.svg`
- `bus_map_<站名>.html`
- `bus_map_<站名>_lines_only.svg`

If the stop keyword is too broad, the script lists the matched stop records and asks for a more specific name.

