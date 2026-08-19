"""Streamlit Community Cloud entry point.

Streamlit Cloud defaults to looking for `streamlit_app.py` at the repo
root. This wrapper adds `src/` to the path (same as running
`src/fast_track/dashboard/app.py` directly) so the app works on Cloud
without a separate `pip install -e .` step.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from fast_track.dashboard.app import main  # noqa: E402

main()
