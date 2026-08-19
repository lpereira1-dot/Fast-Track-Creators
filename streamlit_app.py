"""Streamlit Community Cloud entry point.

Streamlit Cloud defaults to looking for `streamlit_app.py` at the repo
root. This thin wrapper keeps the real dashboard in
`src/fast_track/dashboard/app.py` while giving Cloud a standard path to
deploy from (Settings -> Main file path can be either file).
"""

from fast_track.dashboard.app import main

main()
