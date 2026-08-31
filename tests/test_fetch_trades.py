import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fetch_trades import sanitize_for_sheet


def test_sanitize_for_sheet_removes_non_finite_values():
    payload = [1.0, float('nan'), float('inf'), -float('inf'), None, 'ok', pd.NA]

    assert sanitize_for_sheet(payload) == [1.0, None, None, None, None, 'ok', None]
