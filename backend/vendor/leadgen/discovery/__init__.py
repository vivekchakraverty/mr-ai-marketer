"""Lead discovery — the free replacement for OpenOutreach's paid lead database.

Two pluggable backends behind a common shape:
  * overpass  OpenStreetMap/Overpass — structured, keyless, best for local businesses.
  * searxng   self-hosted metasearch — broader reach across any niche/geography.

`icp.py` turns a campaign's product description into a clause pool and selects which query
to run next (explore/exploit via the GP), faithful in spirit to OpenOutreach's ICP seed +
query selector, adapted to these backends' filter models.
"""
