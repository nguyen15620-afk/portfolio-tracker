import pytest

from portfolio import db, engine as E


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.set_setting(c, "fee_rate", 0.0015)
    db.set_setting(c, "sell_tax_rate", 0.001)
    db.set_setting(c, "div_tax_rate", 0.05)
    return c


@pytest.fixture
def owners(conn):
    return E.add_owner(conn, "Tôi"), E.add_owner(conn, "Mẹ")


def by_owner(rows):
    return {r.name: r for r in rows}


def test_buy_sell_avg_cost_and_realized(conn, owners):
    me, mom = owners
    E.add_cash(conn, "2026-01-01", me, "DEPOSIT", 100_000_000)
    E.add_trade(conn, "2026-01-02", "FPT", "BUY", 100_000, {me: 500}, fee=0, tax=0)
    E.add_trade(conn, "2026-01-03", "FPT", "BUY", 120_000, {me: 500}, fee=0, tax=0)
    pos = E.positions(conn)
    assert pos.iloc[0].avg_cost == pytest.approx(110_000)
    E.add_trade(conn, "2026-01-04", "FPT", "SELL", 130_000, {me: 400}, fee=0, tax=0)
    pos = E.positions(conn).iloc[0]
    assert pos.qty == 600
    assert pos.realized == pytest.approx(400 * 20_000)
    assert pos.avg_cost == pytest.approx(110_000)
    cash = E.cash_balances(conn)[me]
    assert cash == pytest.approx(100_000_000 - 110_000_000 + 52_000_000)


def test_shared_order_fee_split(conn, owners):
    me, mom = owners
    E.add_trade(conn, "2026-01-02", "HPG", "BUY", 25_000, {me: 300, mom: 700})
    rows = conn.execute("SELECT owner_id, fee FROM trades ORDER BY owner_id").fetchall()
    total_fee = round(1000 * 25_000 * 0.0015)
    assert sum(r["fee"] for r in rows) == total_fee
    assert rows[0]["fee"] == round(total_fee * 0.3)


def test_auto_sell_tax(conn, owners):
    me, _ = owners
    E.add_opening_position(conn, "2026-01-01", me, "VNM", 1000, 60_000)
    E.add_trade(conn, "2026-01-05", "VNM", "SELL", 70_000, {me: 1000})
    r = conn.execute("SELECT fee, tax FROM trades WHERE side='SELL'").fetchone()
    assert r["fee"] == round(70_000_000 * 0.0015)
    assert r["tax"] == round(70_000_000 * 0.001)


def test_cannot_oversell(conn, owners):
    me, mom = owners
    E.add_opening_position(conn, "2026-01-01", mom, "VNM", 100, 60_000)
    with pytest.raises(ValueError):
        E.add_trade(conn, "2026-01-05", "VNM", "SELL", 70_000, {me: 100})


def test_dividends_allocated_by_holdings(conn, owners):
    me, mom = owners
    E.add_opening_position(conn, "2026-01-01", me, "FPT", 1000, 100_000)
    E.add_opening_position(conn, "2026-01-01", mom, "FPT", 3000, 100_000)
    # bought on ex-date -> not entitled
    E.add_trade(conn, "2026-06-10", "FPT", "BUY", 100_000, {me: 1000}, fee=0)
    cash = E.apply_corporate_action(conn, "2026-06-10", "FPT", "CASH", 2000, pay_date="2026-07-01")
    assert cash[me] == round(1000 * 2000 * 0.95)
    assert cash[mom] == round(3000 * 2000 * 0.95)
    stock = E.apply_corporate_action(conn, "2026-06-20", "FPT", "STOCK", 15)
    assert stock[me] == 300   # 2000 * 15%
    assert stock[mom] == 450
    pos = E.positions(conn).set_index("owner_id")
    assert pos.loc[mom].qty == 3450
    assert pos.loc[mom].cost == pytest.approx(300_000_000)  # cost unchanged


def test_summary_nav_and_pnl(conn, owners):
    me, mom = owners
    E.add_cash(conn, "2026-01-01", me, "DEPOSIT", 50_000_000)
    E.add_cash(conn, "2026-01-01", mom, "DEPOSIT", 150_000_000)
    E.add_trade(conn, "2026-01-02", "FPT", "BUY", 100_000, {me: 200, mom: 1000}, fee=0)
    s = by_owner(E.summary(conn, {"FPT": 110_000}))
    assert s["Tôi"].nav == pytest.approx(50_000_000 + 200 * 10_000)
    assert s["Mẹ"].pnl == pytest.approx(1000 * 10_000)
    assert s["Mẹ"].pnl_pct == pytest.approx(10_000_000 / 150_000_000)


def test_transfers_preserve_totals(conn, owners):
    me, mom = owners
    E.add_cash(conn, "2026-01-01", mom, "DEPOSIT", 100_000_000)
    E.add_opening_position(conn, "2026-01-01", mom, "MWG", 1000, 50_000)
    E.transfer_cash(conn, "2026-01-02", mom, me, 10_000_000)
    E.transfer_shares(conn, "2026-01-02", mom, me, "MWG", 400)
    s = by_owner(E.summary(conn, {"MWG": 50_000}))
    assert s["Tôi"].nav == pytest.approx(10_000_000 + 400 * 50_000)
    assert s["Tôi"].pnl == pytest.approx(0)
    assert s["Tôi"].nav + s["Mẹ"].nav == pytest.approx(150_000_000)


def test_reconcile(conn, owners):
    me, mom = owners
    E.add_cash(conn, "2026-01-01", me, "DEPOSIT", 10_000_000)
    E.add_opening_position(conn, "2026-01-01", mom, "FPT", 100, 100_000)
    df = E.reconcile(conn, 10_000_000, {"FPT": 100}).set_index("item")
    assert df.ok.all()
    df = E.reconcile(conn, 9_000_000, {"FPT": 100, "HPG": 50}).set_index("item")
    assert not df.loc["Tiền mặt"].ok and not df.loc["HPG"].ok


def test_delete_group(conn, owners):
    me, mom = owners
    gid = E.add_trade(conn, "2026-01-02", "HPG", "BUY", 25_000, {me: 100, mom: 100})
    E.delete_group(conn, gid)
    assert E.positions(conn).empty


def test_performance_by_period(conn, owners):
    me, mom = owners
    E.add_cash(conn, "2026-01-01", me, "DEPOSIT", 50_000_000)
    E.add_trade(conn, "2026-01-02", "HPG", "BUY", 20_000, {me: 1000}, fee=0, tax=0)
    E.add_trade(conn, "2026-01-15", "HPG", "SELL", 25_000, {me: 1000}, fee=0, tax=0)
    # Cash dividend in Feb
    E.add_cash(conn, "2026-02-10", me, "DIVIDEND", 1_000_000, symbol="HPG")

    df_m = E.performance_by_period(conn, period="month", owner_id=me)
    assert len(df_m) == 2
    row_jan = df_m[df_m.period == "2026-01"].iloc[0]
    assert row_jan.num_trades == 1
    assert row_jan.win_trades == 1
    assert row_jan.realized == pytest.approx(5_000_000)
    assert row_jan.dividend == 0.0

    row_feb = df_m[df_m.period == "2026-02"].iloc[0]
    assert row_feb.num_trades == 0
    assert row_feb.dividend == pytest.approx(1_000_000)
    assert row_feb.total_profit == pytest.approx(1_000_000)

    df_y = E.performance_by_period(conn, period="year", owner_id=me)
    assert len(df_y) == 1
    row_y = df_y.iloc[0]
    assert row_y.period == "2026"
    assert row_y.total_profit == pytest.approx(6_000_000)
