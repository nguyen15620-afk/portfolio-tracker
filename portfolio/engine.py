"""Business logic: recording transactions and computing positions, P&L and NAV per owner.

Cost basis uses the weighted-average method (the one Vietnamese brokers display).
Buy fees are capitalised into cost; sell fee + sell tax reduce realised P&L.
"""
from __future__ import annotations

import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date as _date
from typing import Iterable, Mapping

import pandas as pd

from .db import get_setting, tx

CASH_SIGN = {
    "OPENING": 1,
    "DEPOSIT": 1,
    "WITHDRAW": -1,
    "TRANSFER_IN": 1,
    "TRANSFER_OUT": -1,
    "DIVIDEND": 1,
    "INTEREST": 1,
    "FEE": -1,
    "ADJUST": 1,  # amount is given already signed
}
# Cash types that represent capital put in / taken out by the owner (not performance).
CONTRIB_TYPES = ("OPENING", "DEPOSIT", "WITHDRAW", "TRANSFER_IN", "TRANSFER_OUT")

TRADE_SIDES = ("BUY", "SELL", "OPEN", "BONUS", "TRANSFER_IN", "TRANSFER_OUT")


def _gid() -> str:
    return uuid.uuid4().hex[:12]


def _d(d) -> str:
    if d is None:
        return _date.today().isoformat()
    if isinstance(d, str):
        return d[:10]
    return d.isoformat()[:10]


# --------------------------------------------------------------------------- owners
def add_owner(conn: sqlite3.Connection, name: str) -> int:
    with tx(conn):
        cur = conn.execute("INSERT INTO owners(name) VALUES (?)", (name.strip(),))
    return cur.lastrowid


def list_owners(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM owners ORDER BY id")}


# --------------------------------------------------------------------------- cash
def add_cash(conn, date, owner_id: int, type_: str, amount: float, note: str = "",
             symbol: str | None = None, group_id: str | None = None) -> None:
    type_ = type_.upper()
    if type_ not in CASH_SIGN:
        raise ValueError(f"Loại giao dịch tiền không hợp lệ: {type_}")
    signed = amount if type_ == "ADJUST" else CASH_SIGN[type_] * abs(amount)
    with tx(conn):
        conn.execute(
            "INSERT INTO cash_tx(date, owner_id, type, amount, symbol, group_id, note) "
            "VALUES (?,?,?,?,?,?,?)",
            (_d(date), owner_id, type_, signed, symbol, group_id, note),
        )


def transfer_cash(conn, date, from_owner: int, to_owner: int, amount: float, note: str = "") -> str:
    if from_owner == to_owner:
        raise ValueError("Người chuyển và người nhận phải khác nhau")
    gid = _gid()
    add_cash(conn, date, from_owner, "TRANSFER_OUT", amount, note, group_id=gid)
    add_cash(conn, date, to_owner, "TRANSFER_IN", amount, note, group_id=gid)
    return gid


# --------------------------------------------------------------------------- trades
def default_fee_tax(conn, side: str, qty: float, price: float) -> tuple[float, float]:
    value = qty * price
    fee = round(value * get_setting(conn, "fee_rate"))
    tax = round(value * get_setting(conn, "sell_tax_rate")) if side == "SELL" else 0
    return fee, tax


def add_trade(conn, date, symbol: str, side: str, price: float,
              allocations: Mapping[int, float], fee: float | None = None,
              tax: float | None = None, note: str = "", check_holdings: bool = True) -> str:
    """Record one matched order, split between owners.

    allocations: {owner_id: qty}. Fee/tax (auto-computed if None) are split pro-rata by qty.
    """
    side = side.upper()
    symbol = symbol.upper().strip()
    if side not in ("BUY", "SELL"):
        raise ValueError("side phải là BUY hoặc SELL")
    allocations = {o: float(q) for o, q in allocations.items() if q and float(q) > 0}
    total_qty = sum(allocations.values())
    if total_qty <= 0:
        raise ValueError("Khối lượng phải > 0")
    auto_fee, auto_tax = default_fee_tax(conn, side, total_qty, price)
    fee = auto_fee if fee is None else fee
    tax = auto_tax if tax is None else tax

    if side == "SELL" and check_holdings:
        pos = positions(conn, as_of=_d(date))
        for o, q in allocations.items():
            held = _held(pos, o, symbol)
            if q > held + 1e-9:
                raise ValueError(f"Owner {o} chỉ có {held:g} {symbol}, không thể bán {q:g}")

    gid = _gid()
    items = list(allocations.items())
    fee_left, tax_left = fee, tax
    with tx(conn):
        for i, (o, q) in enumerate(items):
            if i == len(items) - 1:
                f, t = fee_left, tax_left
            else:
                f = round(fee * q / total_qty)
                t = round(tax * q / total_qty)
                fee_left -= f
                tax_left -= t
            conn.execute(
                "INSERT INTO trades(date, owner_id, symbol, side, qty, price, fee, tax, group_id, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (_d(date), o, symbol, side, q, price, f, t, gid, note),
            )
    return gid


def add_opening_position(conn, date, owner_id: int, symbol: str, qty: float,
                         avg_price: float, note: str = "Số dư đầu kỳ") -> None:
    with tx(conn):
        conn.execute(
            "INSERT INTO trades(date, owner_id, symbol, side, qty, price, group_id, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (_d(date), owner_id, symbol.upper().strip(), "OPEN", qty, avg_price, _gid(), note),
        )


def transfer_shares(conn, date, from_owner: int, to_owner: int, symbol: str, qty: float,
                    price: float | None = None, note: str = "") -> str:
    """Move shares between owners. Default price = sender's average cost (no P&L)."""
    symbol = symbol.upper().strip()
    pos = positions(conn, as_of=_d(date))
    held = _held(pos, from_owner, symbol)
    if qty > held + 1e-9:
        raise ValueError(f"Chỉ có {held:g} {symbol} để chuyển")
    if price is None:
        row = pos[(pos.owner_id == from_owner) & (pos.symbol == symbol)].iloc[0]
        price = row.avg_cost
    gid = _gid()
    with tx(conn):
        for o, side in ((from_owner, "TRANSFER_OUT"), (to_owner, "TRANSFER_IN")):
            conn.execute(
                "INSERT INTO trades(date, owner_id, symbol, side, qty, price, group_id, note) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (_d(date), o, symbol, side, qty, price, gid, note),
            )
    return gid


def delete_group(conn, group_id: str) -> None:
    """Delete every row (trades, cash, corporate action) belonging to a group."""
    with tx(conn):
        conn.execute("DELETE FROM trades WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM cash_tx WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM corporate_actions WHERE group_id = ?", (group_id,))


def delete_row(conn, table: str, row_id: int) -> None:
    if table not in ("trades", "cash_tx"):
        raise ValueError(table)
    with tx(conn):
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


# --------------------------------------------------------------------------- corporate actions
def apply_corporate_action(conn, ex_date, symbol: str, kind: str, value: float,
                           pay_date=None, note: str = "") -> dict[int, float]:
    """Allocate a dividend to each owner based on holdings on the day before ex-date.

    CASH : value = VND per share, credited (after dividend tax) on pay_date.
    STOCK: value = ratio in % (e.g. 15 means 100 shares -> 15 new shares), fractions dropped.
    Returns {owner_id: amount_or_shares}.
    """
    kind = kind.upper()
    symbol = symbol.upper().strip()
    ex = _d(ex_date)
    pos = positions(conn, as_of=ex, inclusive=False)
    pos = pos[(pos.symbol == symbol) & (pos.qty > 0)]
    gid = _gid()
    result: dict[int, float] = {}
    div_tax = get_setting(conn, "div_tax_rate")
    with tx(conn):
        conn.execute(
            "INSERT INTO corporate_actions(ex_date, symbol, kind, value, pay_date, group_id, note) "
            "VALUES (?,?,?,?,?,?,?)",
            (ex, symbol, kind, value, _d(pay_date) if pay_date else None, gid, note),
        )
        for r in pos.itertuples():
            if kind == "CASH":
                amt = round(r.qty * value * (1 - div_tax))
                conn.execute(
                    "INSERT INTO cash_tx(date, owner_id, type, amount, symbol, group_id, note) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (_d(pay_date) if pay_date else ex, r.owner_id, "DIVIDEND", amt, symbol, gid,
                     note or f"Cổ tức tiền {value:g}đ/cp"),
                )
                result[r.owner_id] = amt
            elif kind == "STOCK":
                new = math.floor(r.qty * value / 100 + 1e-9)
                if new > 0:
                    conn.execute(
                        "INSERT INTO trades(date, owner_id, symbol, side, qty, price, group_id, note) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (ex, r.owner_id, symbol, "BONUS", new, 0, gid,
                         note or f"Cổ tức CP {value:g}%"),
                    )
                result[r.owner_id] = new
            else:
                raise ValueError("kind phải là CASH hoặc STOCK")
    return result


# --------------------------------------------------------------------------- computations
def load_trades(conn, as_of: str | None = None, inclusive: bool = True) -> pd.DataFrame:
    q = "SELECT * FROM trades"
    args: list = []
    if as_of:
        q += " WHERE date <= ?" if inclusive else " WHERE date < ?"
        args.append(as_of)
    q += " ORDER BY date, id"
    return pd.read_sql_query(q, conn, params=args)


def load_cash(conn, as_of: str | None = None) -> pd.DataFrame:
    q = "SELECT * FROM cash_tx"
    args: list = []
    if as_of:
        q += " WHERE date <= ?"
        args.append(as_of)
    q += " ORDER BY date, id"
    return pd.read_sql_query(q, conn, params=args)


def positions(conn, as_of: str | None = None, inclusive: bool = True) -> pd.DataFrame:
    """Per (owner, symbol): qty, total cost, avg cost, realised P&L."""
    trades = load_trades(conn, as_of, inclusive)
    state: dict[tuple[int, str], dict] = {}
    for t in trades.itertuples():
        s = state.setdefault((t.owner_id, t.symbol), {"qty": 0.0, "cost": 0.0, "realized": 0.0})
        if t.side in ("BUY",):
            s["qty"] += t.qty
            s["cost"] += t.qty * t.price + t.fee
        elif t.side in ("OPEN", "TRANSFER_IN"):
            s["qty"] += t.qty
            s["cost"] += t.qty * t.price
        elif t.side == "BONUS":
            s["qty"] += t.qty
        elif t.side in ("SELL", "TRANSFER_OUT"):
            avg = s["cost"] / s["qty"] if s["qty"] else 0.0
            cost_out = avg * t.qty
            if t.side == "SELL":
                s["realized"] += t.qty * t.price - t.fee - t.tax - cost_out
            s["cost"] -= cost_out
            s["qty"] -= t.qty
            if abs(s["qty"]) < 1e-9:
                s["qty"], s["cost"] = 0.0, 0.0
    rows = [
        {"owner_id": o, "symbol": sym, "qty": s["qty"], "cost": s["cost"],
         "avg_cost": s["cost"] / s["qty"] if s["qty"] else 0.0, "realized": s["realized"]}
        for (o, sym), s in state.items()
    ]
    return pd.DataFrame(rows, columns=["owner_id", "symbol", "qty", "cost", "avg_cost", "realized"])


def closed_trades_log(conn, as_of: str | None = None) -> pd.DataFrame:
    """Detailed log of each closed/sell trade with calculated cost basis, proceeds, and realized P&L."""
    trades = load_trades(conn, as_of)
    owners = list_owners(conn)
    state: dict[tuple[int, str], dict] = {}
    records: list[dict] = []

    for t in trades.itertuples():
        s = state.setdefault((t.owner_id, t.symbol), {"qty": 0.0, "cost": 0.0})
        if t.side in ("BUY",):
            s["qty"] += t.qty
            s["cost"] += t.qty * t.price + t.fee
        elif t.side in ("OPEN", "TRANSFER_IN"):
            s["qty"] += t.qty
            s["cost"] += t.qty * t.price
        elif t.side == "BONUS":
            s["qty"] += t.qty
        elif t.side in ("SELL", "TRANSFER_OUT"):
            avg = s["cost"] / s["qty"] if s["qty"] else 0.0
            cost_out = avg * t.qty
            if t.side == "SELL":
                revenue = t.qty * t.price - t.fee - t.tax
                pnl = revenue - cost_out
                ret_pct = pnl / cost_out if cost_out else 0.0
                records.append({
                    "date": t.date,
                    "year": str(t.date)[:4],
                    "month": str(t.date)[:7],
                    "owner_id": t.owner_id,
                    "owner": owners.get(t.owner_id, str(t.owner_id)),
                    "symbol": t.symbol,
                    "qty": t.qty,
                    "sell_price": t.price,
                    "avg_cost": avg,
                    "cost_out": cost_out,
                    "fee": t.fee,
                    "tax": t.tax,
                    "revenue": revenue,
                    "realized": pnl,
                    "return_pct": ret_pct,
                    "note": t.note or "",
                })
            s["cost"] -= cost_out
            s["qty"] -= t.qty
            if abs(s["qty"]) < 1e-9:
                s["qty"], s["cost"] = 0.0, 0.0

    cols = ["date", "year", "month", "owner_id", "owner", "symbol", "qty", "sell_price",
            "avg_cost", "cost_out", "fee", "tax", "revenue", "realized", "return_pct", "note"]
    return pd.DataFrame(records, columns=cols)


def performance_by_period(conn, period: str = "month", owner_id: int | None = None) -> pd.DataFrame:
    """Aggregate trading performance (realized P&L, dividend, win/loss rate) by month ('YYYY-MM') or year ('YYYY')."""
    period_col = "year" if period == "year" else "month"
    df_closed = closed_trades_log(conn)
    if owner_id is not None:
        df_closed = df_closed[df_closed.owner_id == owner_id]

    # Cash dividends
    cash = load_cash(conn)
    div_df = cash[cash.type == "DIVIDEND"].copy()
    if owner_id is not None:
        div_df = div_df[div_df.owner_id == owner_id]

    if not div_df.empty:
        div_df["period"] = div_df["date"].str[:4] if period == "year" else div_df["date"].str[:7]
        div_agg = div_df.groupby("period")["amount"].sum().to_dict()
    else:
        div_agg = {}

    if df_closed.empty and not div_agg:
        return pd.DataFrame(columns=[
            "period", "num_trades", "win_trades", "loss_trades", "win_rate",
            "cost_out", "revenue", "realized", "dividend", "total_profit", "return_pct"
        ])

    periods = sorted(set(df_closed[period_col].dropna().unique()) | set(div_agg.keys()))
    rows = []
    for p in periods:
        sub = df_closed[df_closed[period_col] == p] if not df_closed.empty else pd.DataFrame()
        n_trades = len(sub)
        n_win = int((sub.realized > 0).sum()) if n_trades else 0
        n_loss = int((sub.realized < 0).sum()) if n_trades else 0
        win_rate = (n_win / n_trades * 100) if n_trades else 0.0
        c_out = float(sub.cost_out.sum()) if n_trades else 0.0
        rev = float(sub.revenue.sum()) if n_trades else 0.0
        realized = float(sub.realized.sum()) if n_trades else 0.0
        div = float(div_agg.get(p, 0.0))
        total_profit = realized + div
        ret_pct = (total_profit / c_out * 100) if c_out > 0 else 0.0

        rows.append({
            "period": p,
            "num_trades": n_trades,
            "win_trades": n_win,
            "loss_trades": n_loss,
            "win_rate": win_rate,
            "cost_out": c_out,
            "revenue": rev,
            "realized": realized,
            "dividend": div,
            "total_profit": total_profit,
            "return_pct": ret_pct,
        })

    return pd.DataFrame(rows)


def nav_change_by_period(conn, period: str = "month", owner_id: int | None = None) -> pd.DataFrame:
    """Calculate start-of-period vs end-of-period NAV changes, net cash flows, and performance.
    
    period: 'month' (e.g. 2025-01) or 'year' (e.g. 2025).
    owner_id: None for total portfolio, or specific owner_id.
    """
    import calendar

    tr = load_trades(conn)
    cash = load_cash(conn)
    if tr.empty and cash.empty:
        return pd.DataFrame(columns=[
            "period", "nav_start", "net_flow", "nav_end", "diff", "pct", "invest_gain", "invest_pct"
        ])

    p_df = pd.read_sql_query("SELECT symbol, date, close, source FROM prices", conn)
    # Order so real market quotes (vnstock) take priority over manual / trade fallbacks
    p_df = p_df.sort_values("date")

    def _get_prices(target_date: str) -> dict[str, float]:
        sub = p_df[p_df.date <= target_date]
        if sub.empty:
            return {}
        return sub.groupby("symbol").close.last().to_dict()

    dates_all = sorted(set(tr["date"].tolist() + cash["date"].tolist()))
    min_year = int(dates_all[0][:4]) if dates_all else _date.today().year
    today_str = _date.today().isoformat()
    max_year = int(today_str[:4])

    rows = []
    if period == "year":
        prev_nav = 0.0
        for y in range(min_year, max_year + 1):
            y_str = str(y)
            end_d = min(f"{y_str}-12-31", today_str)
            px_e = _get_prices(end_d)
            summ_e = summary(conn, px_e, end_d)
            nav_e = sum(s.nav for s in summ_e if owner_id is None or s.owner_id == owner_id)

            cash_y = cash[cash.date.str.startswith(y_str)]
            if owner_id is not None:
                cash_y = cash_y[cash_y.owner_id == owner_id]
            inflow = float(cash_y[cash_y.type.isin(["OPENING", "DEPOSIT"])].amount.sum())
            outflow = float(cash_y[cash_y.type == "WITHDRAW"].amount.sum())
            net_flow = inflow - outflow

            nav_s = prev_nav
            diff = nav_e - nav_s
            pct = (diff / nav_s * 100) if nav_s > 0 else 0.0
            invest_gain = diff - net_flow
            invest_pct = (invest_gain / (nav_s + net_flow) * 100) if (nav_s + net_flow) > 0 else 0.0

            rows.append({
                "period": y_str,
                "nav_start": nav_s,
                "net_flow": net_flow,
                "nav_end": nav_e,
                "diff": diff,
                "pct": pct,
                "invest_gain": invest_gain,
                "invest_pct": invest_pct,
            })
            prev_nav = nav_e
    else:  # month
        months = []
        for y in range(min_year, max_year + 1):
            for m in range(1, 13):
                ym = f"{y:04d}-{m:02d}"
                if ym >= dates_all[0][:7] and ym <= today_str[:7]:
                    months.append(ym)
        prev_nav = 0.0
        for ym in months:
            y_int, m_int = map(int, ym.split("-"))
            last_day = calendar.monthrange(y_int, m_int)[1]
            end_d = min(f"{ym}-{last_day:02d}", today_str)
            px_e = _get_prices(end_d)
            summ_e = summary(conn, px_e, end_d)
            nav_e = sum(s.nav for s in summ_e if owner_id is None or s.owner_id == owner_id)

            cash_m = cash[cash.date.str.startswith(ym)]
            if owner_id is not None:
                cash_m = cash_m[cash_m.owner_id == owner_id]
            inflow = float(cash_m[cash_m.type.isin(["OPENING", "DEPOSIT"])].amount.sum())
            outflow = float(cash_m[cash_m.type == "WITHDRAW"].amount.sum())
            net_flow = inflow - outflow

            nav_s = prev_nav
            diff = nav_e - nav_s
            pct = (diff / nav_s * 100) if nav_s > 0 else 0.0
            invest_gain = diff - net_flow
            invest_pct = (invest_gain / (nav_s + net_flow) * 100) if (nav_s + net_flow) > 0 else 0.0

            rows.append({
                "period": ym,
                "nav_start": nav_s,
                "net_flow": net_flow,
                "nav_end": nav_e,
                "diff": diff,
                "pct": pct,
                "invest_gain": invest_gain,
                "invest_pct": invest_pct,
            })
            prev_nav = nav_e

    return pd.DataFrame(rows)



def _held(pos: pd.DataFrame, owner_id: int, symbol: str) -> float:
    m = pos[(pos.owner_id == owner_id) & (pos.symbol == symbol)]
    return float(m.qty.sum()) if not m.empty else 0.0


def cash_balances(conn, as_of: str | None = None) -> dict[int, float]:
    out = {o: 0.0 for o in list_owners(conn)}
    cash = load_cash(conn, as_of)
    for r in cash.itertuples():
        out[r.owner_id] = out.get(r.owner_id, 0.0) + r.amount
    for t in load_trades(conn, as_of).itertuples():
        if t.side == "BUY":
            out[t.owner_id] -= t.qty * t.price + t.fee
        elif t.side == "SELL":
            out[t.owner_id] += t.qty * t.price - t.fee - t.tax
    return out


def net_contributions(conn, as_of: str | None = None) -> dict[int, float]:
    """Capital each owner has put in (cash in - cash out + shares brought in at cost).
    Can be explicitly overridden via setting 'custom_net_contrib_{owner_id}' if needed.
    """
    out = {o: 0.0 for o in list_owners(conn)}
    cash = load_cash(conn, as_of)
    for r in cash[cash.type.isin(CONTRIB_TYPES)].itertuples():
        out[r.owner_id] += r.amount
    for t in load_trades(conn, as_of).itertuples():
        if t.side in ("OPEN", "TRANSFER_IN"):
            out[t.owner_id] += t.qty * t.price
        elif t.side == "TRANSFER_OUT":
            out[t.owner_id] -= t.qty * t.price
    for o in list_owners(conn):
        custom = get_setting(conn, f"custom_net_contrib_{o}")
        if custom is not None:
            try:
                out[o] = float(custom)
            except (ValueError, TypeError):
                pass
    return out


def dividends_received(conn, as_of: str | None = None) -> dict[int, float]:
    out = {o: 0.0 for o in list_owners(conn)}
    cash = load_cash(conn, as_of)
    for r in cash[cash.type == "DIVIDEND"].itertuples():
        out[r.owner_id] += r.amount
    return out


def holdings_table(conn, prices: Mapping[str, float], as_of: str | None = None) -> pd.DataFrame:
    pos = positions(conn, as_of)
    owners = list_owners(conn)
    if pos.empty:
        return pos.assign(owner="", price=0.0, market_value=0.0, unrealized=0.0, unrealized_pct=0.0)
    pos["owner"] = pos.owner_id.map(owners)
    pos["price"] = pos.symbol.map(lambda s: prices.get(s, float("nan")))
    pos["price"] = pos["price"].fillna(pos["avg_cost"])  # fallback if price unknown
    pos["market_value"] = pos.qty * pos.price
    pos["unrealized"] = pos.market_value - pos.cost
    pos["unrealized_pct"] = pos.apply(lambda r: r.unrealized / r.cost if r.cost else 0.0, axis=1)
    return pos


@dataclass
class OwnerSummary:
    owner_id: int
    name: str
    cash: float
    market_value: float
    nav: float
    net_contrib: float
    pnl: float
    pnl_pct: float
    realized: float
    unrealized: float
    dividends: float


def summary(conn, prices: Mapping[str, float], as_of: str | None = None) -> list[OwnerSummary]:
    owners = list_owners(conn)
    cash = cash_balances(conn, as_of)
    contrib = net_contributions(conn, as_of)
    divs = dividends_received(conn, as_of)
    hold = holdings_table(conn, prices, as_of)
    res = []
    for o, name in owners.items():
        h = hold[hold.owner_id == o] if not hold.empty else hold
        mv = float(h.market_value.sum()) if not h.empty else 0.0
        realized = float(h.realized.sum()) if not h.empty else 0.0
        unreal = float(h[h.qty > 0].unrealized.sum()) if not h.empty else 0.0
        nav = cash[o] + mv
        pnl = nav - contrib[o]
        res.append(OwnerSummary(o, name, cash[o], mv, nav, contrib[o], pnl,
                                pnl / contrib[o] if contrib[o] else 0.0, realized, unreal, divs[o]))
    return res


def reconcile(conn, actual_cash: float, actual_holdings: Mapping[str, float],
              as_of: str | None = None) -> pd.DataFrame:
    """Compare the sum of owners' sub-ledgers against the real broker balances."""
    cash = sum(cash_balances(conn, as_of).values())
    pos = positions(conn, as_of)
    ledger = pos.groupby("symbol").qty.sum().to_dict() if not pos.empty else {}
    rows = [{"item": "Tiền mặt", "ledger": cash, "actual": actual_cash}]
    for sym in sorted(set(ledger) | set(actual_holdings)):
        l, a = ledger.get(sym, 0.0), float(actual_holdings.get(sym, 0.0))
        if l == 0 and a == 0:
            continue
        rows.append({"item": sym, "ledger": l, "actual": a})
    df = pd.DataFrame(rows)
    df["diff"] = df.actual - df.ledger
    df["ok"] = df["diff"].abs() < 1
    return df


def record_snapshot(conn, prices: Mapping[str, float], as_of: str | None = None) -> None:
    d = _d(as_of)
    with tx(conn):
        for s in summary(conn, prices, d):
            conn.execute(
                "INSERT OR REPLACE INTO snapshots(date, owner_id, cash, market_value, nav, net_contrib) "
                "VALUES (?,?,?,?,?,?)",
                (d, s.owner_id, s.cash, s.market_value, s.nav, s.net_contrib),
            )


def nav_history(conn, price_history: pd.DataFrame, dates: Iterable[str]) -> pd.DataFrame:
    """Rebuild NAV per owner for each date using a price history frame
    with columns [symbol, date, close]."""
    ph = price_history.copy()
    ph["date"] = ph["date"].astype(str).str[:10]
    ph = ph.sort_values("date")
    rows = []
    for d in dates:
        last = ph[ph.date <= d].groupby("symbol").close.last().to_dict()
        for s in summary(conn, last, d):
            rows.append({"date": d, "owner": s.name, "nav": s.nav,
                         "net_contrib": s.net_contrib, "pnl": s.pnl})
    return pd.DataFrame(rows)
