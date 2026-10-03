"""Smoke test: render every page of the Streamlit app against a seeded temp DB."""
import os
from pathlib import Path

import pytest

PAGES = ["📊 Tổng quan", "📈 Hiệu suất đầu tư", "🛒 Lệnh mua/bán", "💵 Tiền & chuyển nhượng", "🎁 Cổ tức",
         "🏁 Số dư đầu kỳ", "🔍 Đối soát", "📒 Sổ giao dịch", "📥 Import", "⚙️ Cài đặt"]


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "t.db"
    os.environ["PORTFOLIO_DB"] = str(path)
    from portfolio import db, engine as E, prices as P
    c = db.connect(path)
    me, mom = E.add_owner(c, "Tôi"), E.add_owner(c, "Mẹ")
    E.add_cash(c, "2026-09-01", me, "OPENING", 50_000_000)
    E.add_cash(c, "2026-09-01", mom, "OPENING", 200_000_000)
    E.add_opening_position(c, "2026-09-01", mom, "FPT", 1000, 60_000)
    E.add_trade(c, "2026-09-05", "HPG", "BUY", 20_000, {me: 500, mom: 1500})
    P.set_manual_price(c, "FPT", 62_100)
    P.set_manual_price(c, "HPG", 19_900)
    c.close()
    yield path
    os.environ.pop("PORTFOLIO_DB", None)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(seeded, page):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception
