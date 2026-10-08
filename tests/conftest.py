import os
import pytest

# Ensure Google Sheets synchronization is strictly disabled during all automated test runs
os.environ["PORTFOLIO_DISABLE_GSHEET_SYNC"] = "1"
os.environ["PYTEST_CURRENT_TEST"] = "1"
