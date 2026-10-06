"""Streamlit UI: theo dõi danh mục & tài sản ròng của từng người trên 1 tài khoản chung.

Chạy:  streamlit run app.py
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from portfolio import db, engine as E, importer, prices as P

st.set_page_config(page_title="Danh mục gia đình", page_icon="📈", layout="wide")


@st.cache_resource
def get_conn():
    return db.connect()


conn = get_conn()


# ------------------------------------------------------------------ helpers
def vnd(x: float) -> str:
    return f"{x:,.0f} đ".replace(",", ".")


def pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def owners() -> dict[int, str]:
    return E.list_owners(conn)


def all_symbols() -> list[str]:
    pos = E.positions(conn)
    return sorted(pos[pos.qty > 0].symbol.unique().tolist()) if not pos.empty else []


def money_input(label: str, key: str, value: float = 0.0) -> float:
    return st.number_input(label, min_value=0.0, value=float(value), step=1_000_000.0,
                           format="%.0f", key=key)


def ok(msg: str):
    st.toast(msg, icon="✅")


# ------------------------------------------------------------------ first run
if not owners():
    st.title("📈 Thiết lập ban đầu")
    st.write("Nhập tên những người cùng dùng chung tài khoản.")
    with st.form("init"):
        a = st.text_input("Người 1", "Tôi")
        b = st.text_input("Người 2", "Mẹ")
        if st.form_submit_button("Tạo"):
            for n in (a, b):
                if n.strip():
                    E.add_owner(conn, n)
            st.rerun()
    st.stop()


OW = owners()
OW_IDS = list(OW)

page = st.sidebar.radio(
    "Menu",
    ["📊 Tổng quan", "📈 Hiệu suất đầu tư", "🛒 Lệnh mua/bán", "💵 Tiền & chuyển nhượng", "🎁 Cổ tức",
     "🏁 Số dư đầu kỳ", "🔍 Đối soát", "📒 Sổ giao dịch", "📥 Import", "⚙️ Cài đặt"],
)
st.sidebar.caption("Giá cổ phiếu nhập theo **nghìn đồng** (vd 62.7 = 62.700đ) hoặc VND đầy đủ.")

st.sidebar.divider()
st.sidebar.markdown("☁️ **Lưu trữ Google Sheets**")
sb_col1, sb_col2 = st.sidebar.columns(2)
if sb_col1.button("📥 Tải về", help="Kéo dữ liệu mới nhất từ Google Sheets về app"):
    try:
        from portfolio import gsheet_sync
        ok_sync = gsheet_sync.pull_from_sheets_to_sqlite(conn)
        if ok_sync:
            st.sidebar.success("Đã đồng bộ!")
            st.rerun()
        else:
            st.sidebar.warning("Chưa có kết nối")
    except Exception as e:
        st.sidebar.error(str(e))

if sb_col2.button("📤 Tải lên", help="Lưu toàn bộ database hiện tại lên Google Sheets"):
    try:
        from portfolio import gsheet_sync
        gsheet_sync.push_all_to_sheet(conn)
        st.sidebar.success("Đã lưu!")
    except Exception as e:
        st.sidebar.error(str(e))


def price_input(label: str, key: str) -> float:
    v = st.number_input(label, min_value=0.0, step=0.05, format="%.2f", key=key)
    return v * 1000 if 0 < v < 1000 else v


# ================================================================== OVERVIEW
if page == "📊 Tổng quan":
    st.title("📊 Tổng quan tài sản")
    syms = all_symbols()
    c1, c2 = st.columns([1, 4])
    refresh = c1.button("🔄 Cập nhật giá", type="primary")
    if refresh:
        with st.spinner("Đang lấy giá từ vnstock..."):
            try:
                P.fetch_live_prices(conn, syms)
            except Exception as e:
                st.error(f"Không lấy được giá: {e}")
    cached = P.cached_prices(conn, syms)
    px_map = {s: v[0] for s, v in cached.items()}
    missing = [s for s in syms if s not in px_map]
    if cached:
        last = max(v[1] for v in cached.values())
        c2.caption(f"Giá cập nhật gần nhất: {last}. "
                   + (f"⚠️ Chưa có giá: {', '.join(missing)} (đang dùng giá vốn)" if missing else ""))
    elif syms:
        c2.warning("Chưa có giá thị trường — bấm *Cập nhật giá*. Đang tạm dùng giá vốn.")

    summ = E.summary(conn, px_map)
    total_nav = sum(s.nav for s in summ)
    E.record_snapshot(conn, px_map)

    cols = st.columns(len(summ) + 1)
    # Fetch breakdown of contributions per owner
    cash_df_all = E.load_cash(conn)
    for col, s in zip(cols, summ):
        with col.container(border=True):
            st.subheader(s.name)
            st.metric("Tài sản ròng (NAV)", vnd(s.nav), f"{vnd(s.pnl)} ({pct(s.pnl_pct)})")
            
            # Show breakdown of contributions
            sub_cash = cash_df_all[cash_df_all.owner_id == s.owner_id] if not cash_df_all.empty else pd.DataFrame()
            open_amt = float(sub_cash[sub_cash.type == "OPENING"]["amount"].sum()) if not sub_cash.empty else 0.0
            dep_amt = float(sub_cash[sub_cash.type == "DEPOSIT"]["amount"].sum()) if not sub_cash.empty else 0.0
            with_amt = float(sub_cash[sub_cash.type == "WITHDRAW"]["amount"].sum()) if not sub_cash.empty else 0.0
            
            st.write(f"Vốn góp ròng: **{vnd(s.net_contrib)}**")
            with st.expander("🔍 Chi tiết vốn đã nạp", expanded=False):
                st.write(f"• Số dư đầu kỳ: **{vnd(open_amt)}**")
                st.write(f"• Nạp thêm (Deposit): **{vnd(dep_amt)}**")
                if with_amt:
                    st.write(f"• Rút bớt (Withdraw): **{vnd(with_amt)}**")
            
            st.write(f"Tiền mặt khả dụng: **{vnd(s.cash)}**")
            st.write(f"Giá trị CP hiện tại: **{vnd(s.market_value)}**")
            st.write(f"Lãi/lỗ đã chốt: **{vnd(s.realized)}** · Tạm tính: **{vnd(s.unrealized)}**")
            st.write(f"Cổ tức tiền đã nhận: **{vnd(s.dividends)}**")
            if total_nav:
                st.caption(f"Chiếm {s.nav / total_nav:.1%} tài khoản")
            if s.cash < 0:
                st.warning("Tiền mặt âm: người này đang dùng tiền của người khác / margin.")
    with cols[-1].container(border=True):
        st.subheader("Toàn tài khoản")
        st.metric("Tổng NAV", vnd(total_nav), vnd(sum(s.pnl for s in summ)))
        st.write(f"Tiền mặt: **{vnd(sum(s.cash for s in summ))}**")
        st.write(f"Giá trị CP: **{vnd(sum(s.market_value for s in summ))}**")
        if total_nav > 0:
            fig = px.pie(names=[s.name for s in summ], values=[max(s.nav, 0) for s in summ], hole=.5)
            fig.update_layout(height=220, margin=dict(t=0, b=0, l=0, r=0), showlegend=True)
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    hold = E.holdings_table(conn, px_map)
    tabs = st.tabs([s.name for s in summ])
    for tab, s in zip(tabs, summ):
        with tab:
            h = hold[(hold.owner_id == s.owner_id) & (hold.qty > 0)] if not hold.empty else hold
            if h.empty:
                st.info("Chưa có cổ phiếu.")
            else:
                show = pd.DataFrame({
                    "Mã": h.symbol, "KL": h.qty, "Giá vốn TB": h.avg_cost, "Giá hiện tại": h.price,
                    "Giá trị vốn": h.cost, "Giá trị TT": h.market_value,
                    "Lãi/lỗ": h.unrealized, "%": h.unrealized_pct * 100,
                    "Tỷ trọng NAV %": h.market_value / s.nav * 100 if s.nav else 0,
                })
                st.dataframe(
                    show.style.format({"KL": "{:,.0f}", "Giá vốn TB": "{:,.0f}", "Giá hiện tại": "{:,.0f}",
                                       "Giá trị vốn": "{:,.0f}", "Giá trị TT": "{:,.0f}",
                                       "Lãi/lỗ": "{:+,.0f}", "%": "{:+.2f}", "Tỷ trọng NAV %": "{:.1f}"})
                    .map(lambda v: "color: #16a34a" if v > 0 else ("color: #dc2626" if v < 0 else ""),
                         subset=["Lãi/lỗ", "%"]),
                    hide_index=True, use_container_width=True)
            closed = hold[(hold.owner_id == s.owner_id) & (hold.realized != 0)] if not hold.empty else hold
            if not closed.empty:
                st.caption("Lãi/lỗ đã chốt theo mã")
                st.dataframe(pd.DataFrame({"Mã": closed.symbol, "Lãi/lỗ đã chốt": closed.realized})
                             .style.format({"Lãi/lỗ đã chốt": "{:+,.0f}"}), hide_index=True)

    st.divider()
    st.subheader("📈 Lịch sử tài sản ròng")
    hc1, hc2 = st.columns([1, 3])
    days = hc1.selectbox("Khoảng thời gian", [30, 90, 180, 365], index=1, format_func=lambda d: f"{d} ngày")
    if hc1.button("Dựng lại lịch sử từ giá quá khứ"):
        trades = E.load_trades(conn)
        syms_hist = sorted(trades.symbol.unique()) if not trades.empty else []
        start = (date.today() - timedelta(days=days)).isoformat()
        frames = []
        with st.spinner("Đang tải giá lịch sử..."):
            for s in syms_hist:
                try:
                    frames.append(P.fetch_history(s, start, date.today().isoformat()))
                except Exception as e:
                    st.warning(f"{s}: {e}")
        if frames:
            ph = pd.concat(frames)
            dates = sorted(ph.date.unique())
            st.session_state["navhist"] = E.nav_history(conn, ph, dates)
    nh = st.session_state.get("navhist")
    if nh is None:
        snaps = pd.read_sql_query(
            "SELECT s.date, o.name AS owner, s.nav, s.net_contrib FROM snapshots s "
            "JOIN owners o ON o.id = s.owner_id ORDER BY s.date", conn)
        nh = snaps
    if nh is not None and not nh.empty:
        metric = hc2.radio("Hiển thị", ["NAV", "Lãi/lỗ"], horizontal=True)
        nh = nh.copy()
        nh["pnl"] = nh.nav - nh.net_contrib
        fig = px.line(nh, x="date", y="nav" if metric == "NAV" else "pnl", color="owner", markers=True)
        fig.update_layout(yaxis_tickformat=",.0f", height=380)
        hc2.plotly_chart(fig, use_container_width=True)
    else:
        hc2.info("Chưa có dữ liệu lịch sử. Mỗi lần mở trang Tổng quan, hệ thống lưu lại NAV của ngày hôm đó.")


# ================================================================== PERFORMANCE
elif page == "📈 Hiệu suất đầu tư":
    st.title("📈 Hiệu suất đầu tư & Lãi/Lỗ theo thời gian")
    st.caption("Thống kê chi tiết lợi nhuận chốt lời/cắt lỗ từ các lệnh bán và cổ tức tiền theo từng tháng, từng năm.")

    # Owner filter
    owner_options = {"all": "Toàn tài khoản (Tất cả thành viên)"}
    owner_options.update({o: OW[o] for o in OW_IDS})
    sel_owner = st.selectbox("Chọn đối tượng theo dõi", list(owner_options.keys()),
                             format_func=lambda k: owner_options[k])
    filter_oid = None if sel_owner == "all" else sel_owner

    # Summary Metrics across whole history
    df_closed = E.closed_trades_log(conn)
    if filter_oid is not None:
        df_closed = df_closed[df_closed.owner_id == filter_oid]

    total_realized = float(df_closed.realized.sum()) if not df_closed.empty else 0.0
    cash_all = E.load_cash(conn)
    div_df = cash_all[cash_all.type == "DIVIDEND"].copy()
    if filter_oid is not None:
        div_df = div_df[div_df.owner_id == filter_oid]
    total_div = float(div_df.amount.sum()) if not div_df.empty else 0.0
    total_profit_all = total_realized + total_div
    total_trades = len(df_closed)
    win_trades = int((df_closed.realized > 0).sum()) if total_trades else 0
    loss_trades = int((df_closed.realized < 0).sum()) if total_trades else 0
    win_rate = (win_trades / total_trades * 100) if total_trades else 0.0

    mc = st.columns(4)
    mc[0].metric("Tổng lợi nhuận thực nhận", vnd(total_profit_all))
    mc[1].metric("Lãi chốt giao dịch", vnd(total_realized))
    mc[2].metric("Cổ tức tiền mặt", vnd(total_div))
    mc[3].metric("Tỷ lệ thắng (Win Rate)", f"{win_rate:.1f}%", f"{win_trades} thắng / {loss_trades} thua")

    st.divider()

    # Tabs for breakdown
    t_year, t_month, t_symbol, t_log = st.tabs(["📅 Theo từng năm", "📆 Theo từng tháng", "🏷️ Theo mã cổ phiếu", "📜 Nhật ký chốt lời/lỗ"])

    with t_year:
        st.subheader("1. Biến động tài sản ròng (NAV) theo năm")
        st.caption("So sánh NAV đầu năm và cuối năm (có bóc tách dòng tiền nạp/rút) để thấy quy mô tài sản tăng/giảm.")
        df_nav_y = E.nav_change_by_period(conn, period="year", owner_id=filter_oid)
        if not df_nav_y.empty:
            # Bar chart for NAV growth
            fig_ny = px.bar(
                df_nav_y, x="period", y="invest_gain",
                color="invest_gain",
                color_continuous_scale=["#dc2626", "#e5e7eb", "#16a34a"],
                labels={"period": "Năm", "invest_gain": "Lợi nhuận đầu tư thuần (VND)"},
                title="Tăng trưởng tài sản thuần từ đầu tư theo năm (Đã loại trừ vốn nạp/rút)",
                text_auto=True,
            )
            fig_ny.update_layout(height=320, yaxis_tickformat=",.0f", coloraxis_showscale=False)
            st.plotly_chart(fig_ny, use_container_width=True)

            show_ny = pd.DataFrame({
                "Năm": df_nav_y.period,
                "NAV đầu năm": df_nav_y.nav_start,
                "Vốn nạp/rút ròng": df_nav_y.net_flow,
                "NAV cuối năm": df_nav_y.nav_end,
                "Biến động NAV": df_nav_y["diff"],
                "Tăng trưởng NAV %": df_nav_y.pct,
                "Lợi nhuận từ đầu tư": df_nav_y.invest_gain,
                "Tỷ suất sinh lời thuần %": df_nav_y.invest_pct,
            })
            st.dataframe(
                show_ny.style.format({
                    "NAV đầu năm": "{:,.0f}", "Vốn nạp/rút ròng": "{:+,.0f}",
                    "NAV cuối năm": "{:,.0f}", "Biến động NAV": "{:+,.0f}",
                    "Tăng trưởng NAV %": "{:+.2f}%", "Lợi nhuận từ đầu tư": "{:+,.0f}",
                    "Tỷ suất sinh lời thuần %": "{:+.2f}%",
                }).map(lambda v: "color: #16a34a; font-weight: bold" if v > 0 else ("color: #dc2626; font-weight: bold" if v < 0 else ""),
                       subset=["Biến động NAV", "Tăng trưởng NAV %", "Lợi nhuận từ đầu tư", "Tỷ suất sinh lời thuần %"]),
                hide_index=True, use_container_width=True
            )

        st.subheader("2. Chi tiết lãi đã chốt & Cổ tức theo từng năm")
        df_year = E.performance_by_period(conn, period="year", owner_id=filter_oid)
        if df_year.empty:
            st.info("Chưa có giao dịch chốt lời/lỗ hoặc cổ tức trong các năm.")
        else:
            # Bar chart for Yearly Profit
            fig_y = px.bar(
                df_year, x="period", y="total_profit",
                color="total_profit",
                color_continuous_scale=["#dc2626", "#e5e7eb", "#16a34a"],
                labels={"period": "Năm", "total_profit": "Tổng lợi nhuận (VND)"},
                title="Lợi nhuận đã hiện thực hoá theo năm (Lãi chốt lệnh + Cổ tức tiền)",
                text_auto=True,
            )
            fig_y.update_layout(height=320, yaxis_tickformat=",.0f", coloraxis_showscale=False)
            st.plotly_chart(fig_y, use_container_width=True)

            # Table display
            show_y = pd.DataFrame({
                "Năm": df_year.period,
                "Số lệnh bán": df_year.num_trades,
                "Thắng": df_year.win_trades,
                "Thua": df_year.loss_trades,
                "Tỷ lệ thắng %": df_year.win_rate,
                "Giá vốn bán ra": df_year.cost_out,
                "Doanh thu bán": df_year.revenue,
                "Lãi/Lỗ chốt (VND)": df_year.realized,
                "Cổ tức tiền (VND)": df_year.dividend,
                "Tổng lợi nhuận (VND)": df_year.total_profit,
                "Tỷ suất sinh lời %": df_year.return_pct,
            })
            st.dataframe(
                show_y.style.format({
                    "Số lệnh bán": "{:,.0f}", "Thắng": "{:,.0f}", "Thua": "{:,.0f}",
                    "Tỷ lệ thắng %": "{:.1f}%", "Giá vốn bán ra": "{:,.0f}",
                    "Doanh thu bán": "{:,.0f}", "Lãi/Lỗ chốt (VND)": "{:+,.0f}",
                    "Cổ tức tiền (VND)": "{:,.0f}", "Tổng lợi nhuận (VND)": "{:+,.0f}",
                    "Tỷ suất sinh lời %": "{:+.2f}%",
                }).map(lambda v: "color: #16a34a; font-weight: bold" if v > 0 else ("color: #dc2626; font-weight: bold" if v < 0 else ""),
                       subset=["Lãi/Lỗ chốt (VND)", "Tổng lợi nhuận (VND)", "Tỷ suất sinh lời %"]),
                hide_index=True, use_container_width=True
            )

    with t_month:
        st.subheader("1. Biến động tài sản ròng (NAV) đầu tháng so với cuối tháng")
        st.caption("Theo dõi tài sản ròng tăng hay giảm qua từng tháng, bóc tách dòng tiền nộp/rút và lợi nhuận thực tế từ thị trường.")
        df_nav_m = E.nav_change_by_period(conn, period="month", owner_id=filter_oid)
        if not df_nav_m.empty:
            colors_nav_m = ["#16a34a" if p >= 0 else "#dc2626" for p in df_nav_m.invest_gain]
            fig_nm = px.bar(
                df_nav_m, x="period", y="invest_gain",
                labels={"period": "Tháng (YYYY-MM)", "invest_gain": "Lợi nhuận đầu tư thuần (VND)"},
                title="Lợi nhuận đầu tư thuần từng tháng (Thay đổi NAV - Vốn nộp/rút)",
            )
            fig_nm.update_traces(marker_color=colors_nav_m)
            fig_nm.update_layout(height=340, yaxis_tickformat=",.0f")
            st.plotly_chart(fig_nm, use_container_width=True)

            show_nm = pd.DataFrame({
                "Tháng": df_nav_m.period,
                "NAV đầu tháng": df_nav_m.nav_start,
                "Vốn nộp/rút ròng": df_nav_m.net_flow,
                "NAV cuối tháng": df_nav_m.nav_end,
                "Biến động NAV": df_nav_m["diff"],
                "Tăng trưởng NAV %": df_nav_m.pct,
                "Lợi nhuận từ đầu tư": df_nav_m.invest_gain,
                "Tỷ suất sinh lời thuần %": df_nav_m.invest_pct,
            })
            st.dataframe(
                show_nm.sort_values("Tháng", ascending=False).style.format({
                    "NAV đầu tháng": "{:,.0f}", "Vốn nộp/rút ròng": "{:+,.0f}",
                    "NAV cuối tháng": "{:,.0f}", "Biến động NAV": "{:+,.0f}",
                    "Tăng trưởng NAV %": "{:+.2f}%", "Lợi nhuận từ đầu tư": "{:+,.0f}",
                    "Tỷ suất sinh lời thuần %": "{:+.2f}%",
                }).map(lambda v: "color: #16a34a; font-weight: bold" if v > 0 else ("color: #dc2626; font-weight: bold" if v < 0 else ""),
                       subset=["Biến động NAV", "Tăng trưởng NAV %", "Lợi nhuận từ đầu tư", "Tỷ suất sinh lời thuần %"]),
                hide_index=True, use_container_width=True
            )

        st.subheader("2. Chi tiết lệnh bán chốt lời/lỗ & Cổ tức theo từng tháng")
        df_month = E.performance_by_period(conn, period="month", owner_id=filter_oid)
        if df_month.empty:
            st.info("Chưa có giao dịch chốt lời/lỗ hoặc cổ tức theo tháng.")
        else:
            # Bar chart for Monthly Profit
            colors = ["#16a34a" if p >= 0 else "#dc2626" for p in df_month.total_profit]
            fig_m = px.bar(
                df_month, x="period", y="total_profit",
                labels={"period": "Tháng (YYYY-MM)", "total_profit": "Tổng lợi nhuận (VND)"},
                title="Lợi nhuận đã chốt theo từng tháng (Bán cổ phiếu + Cổ tức)",
            )
            fig_m.update_traces(marker_color=colors)
            fig_m.update_layout(height=340, yaxis_tickformat=",.0f")
            st.plotly_chart(fig_m, use_container_width=True)

            # Table display
            show_m = pd.DataFrame({
                "Tháng": df_month.period,
                "Số lệnh": df_month.num_trades,
                "Thắng": df_month.win_trades,
                "Thua": df_month.loss_trades,
                "Tỷ lệ thắng %": df_month.win_rate,
                "Giá vốn": df_month.cost_out,
                "Doanh thu": df_month.revenue,
                "Lãi chốt": df_month.realized,
                "Cổ tức": df_month.dividend,
                "Tổng LN": df_month.total_profit,
                "Tỷ suất %": df_month.return_pct,
            })
            st.dataframe(
                show_m.sort_values("Tháng", ascending=False).style.format({
                    "Số lệnh": "{:,.0f}", "Thắng": "{:,.0f}", "Thua": "{:,.0f}",
                    "Tỷ lệ thắng %": "{:.1f}%", "Giá vốn": "{:,.0f}",
                    "Doanh thu": "{:,.0f}", "Lãi chốt": "{:+,.0f}",
                    "Cổ tức": "{:,.0f}", "Tổng LN": "{:+,.0f}",
                    "Tỷ suất %": "{:+.2f}%",
                }).map(lambda v: "color: #16a34a; font-weight: bold" if v > 0 else ("color: #dc2626; font-weight: bold" if v < 0 else ""),
                       subset=["Lãi chốt", "Tổng LN", "Tỷ suất %"]),
                hide_index=True, use_container_width=True
            )

    with t_symbol:
        st.subheader("Tổng kết lãi/lỗ theo từng mã cổ phiếu đã giao dịch")
        if df_closed.empty:
            st.info("Chưa có giao dịch bán.")
        else:
            sym_grp = df_closed.groupby("symbol").agg(
                trades=("realized", "count"),
                wins=("realized", lambda s: (s > 0).sum()),
                losses=("realized", lambda s: (s < 0).sum()),
                cost_out=("cost_out", "sum"),
                revenue=("revenue", "sum"),
                realized=("realized", "sum"),
            ).reset_index()

            # Add dividends per symbol
            if not div_df.empty:
                div_sym = div_df.groupby("symbol")["amount"].sum().to_dict()
            else:
                div_sym = {}

            sym_grp["dividend"] = sym_grp["symbol"].map(lambda s: div_sym.get(s, 0.0))
            sym_grp["total_profit"] = sym_grp["realized"] + sym_grp["dividend"]
            sym_grp["win_rate"] = sym_grp["wins"] / sym_grp["trades"] * 100
            sym_grp["return_pct"] = sym_grp.apply(lambda r: (r["total_profit"] / r["cost_out"] * 100) if r["cost_out"] else 0.0, axis=1)

            sym_grp = sym_grp.sort_values("total_profit", ascending=False)

            fig_sym = px.bar(
                sym_grp, x="symbol", y="total_profit",
                labels={"symbol": "Mã CK", "total_profit": "Tổng LN (VND)"},
                title="Lợi nhuận theo từng mã cổ phiếu",
            )
            fig_sym.update_traces(marker_color=["#16a34a" if p >= 0 else "#dc2626" for p in sym_grp.total_profit])
            fig_sym.update_layout(height=360, yaxis_tickformat=",.0f")
            st.plotly_chart(fig_sym, use_container_width=True)

            show_sym = pd.DataFrame({
                "Mã": sym_grp.symbol,
                "Số lệnh bán": sym_grp.trades,
                "Thắng / Thua": sym_grp.apply(lambda r: f"{int(r.wins)} / {int(r.losses)}", axis=1),
                "Win Rate %": sym_grp.win_rate,
                "Tổng vốn bán": sym_grp.cost_out,
                "Doanh thu bán": sym_grp.revenue,
                "Lãi chốt": sym_grp.realized,
                "Cổ tức": sym_grp.dividend,
                "Tổng LN": sym_grp.total_profit,
                "Hiệu suất %": sym_grp.return_pct,
            })
            st.dataframe(
                show_sym.style.format({
                    "Số lệnh bán": "{:,.0f}", "Win Rate %": "{:.1f}%",
                    "Tổng vốn bán": "{:,.0f}", "Doanh thu bán": "{:,.0f}",
                    "Lãi chốt": "{:+,.0f}", "Cổ tức": "{:,.0f}",
                    "Tổng LN": "{:+,.0f}", "Hiệu suất %": "{:+.2f}%",
                }).map(lambda v: "color: #16a34a; font-weight: bold" if v > 0 else ("color: #dc2626; font-weight: bold" if v < 0 else ""),
                       subset=["Lãi chốt", "Tổng LN", "Hiệu suất %"]),
                hide_index=True, use_container_width=True
            )

    with t_log:
        st.subheader("Chi tiết từng lệnh bán (đối soát giá vốn & lãi/lỗ)")
        if df_closed.empty:
            st.info("Chưa có lệnh bán nào.")
        else:
            show_log = pd.DataFrame({
                "Ngày": df_closed.date,
                "Người": df_closed.owner,
                "Mã": df_closed.symbol,
                "Khối lượng": df_closed.qty,
                "Giá vốn TB": df_closed.avg_cost,
                "Giá bán": df_closed.sell_price,
                "Tổng vốn": df_closed.cost_out,
                "Thu về (sau thuế phí)": df_closed.revenue,
                "Lãi/Lỗ chốt": df_closed.realized,
                "% Lãi/Lỗ": df_closed.return_pct * 100,
                "Ghi chú": df_closed.note,
            })
            st.dataframe(
                show_log.sort_values("Ngày", ascending=False).style.format({
                    "Khối lượng": "{:,.0f}", "Giá vốn TB": "{:,.0f}", "Giá bán": "{:,.0f}",
                    "Tổng vốn": "{:,.0f}", "Thu về (sau thuế phí)": "{:,.0f}",
                    "Lãi/Lỗ chốt": "{:+,.0f}", "% Lãi/Lỗ": "{:+.2f}%",
                }).map(lambda v: "color: #16a34a" if v > 0 else ("color: #dc2626" if v < 0 else ""),
                       subset=["Lãi/Lỗ chốt", "% Lãi/Lỗ"]),
                hide_index=True, use_container_width=True
            )


# ================================================================== TRADES
elif page == "🛒 Lệnh mua/bán":
    st.title("🛒 Ghi lệnh khớp")
    st.caption("Một lệnh khớp trên tài khoản có thể chia cho nhiều người. Phí/thuế được chia theo khối lượng.")
    with st.form("trade", clear_on_submit=True):
        c = st.columns(4)
        d = c[0].date_input("Ngày khớp", date.today(), format="DD/MM/YYYY")
        sym = c[1].text_input("Mã CK").upper().strip()
        side = c[2].selectbox("Loại", ["BUY", "SELL"], format_func=lambda s: "MUA" if s == "BUY" else "BÁN")
        with c[3]:
            price = price_input("Giá khớp", "tprice")
        st.write("**Khối lượng của từng người**")
        qc = st.columns(len(OW))
        alloc = {o: qc[i].number_input(OW[o], min_value=0, step=100, key=f"q{o}") for i, o in enumerate(OW_IDS)}
        auto = st.checkbox("Tự tính phí & thuế theo cài đặt (VPS: phí 0.15%, thuế bán 0.1%)", value=True)
        fc = st.columns(2)
        total_q = sum(alloc.values())
        cur_fee_rate = db.get_setting(conn, "fee_rate")
        cur_tax_rate = db.get_setting(conn, "sell_tax_rate")
        est_fee = round(total_q * price * cur_fee_rate) if (total_q and price) else 0.0
        est_tax = round(total_q * price * cur_tax_rate) if (total_q and price and side == "SELL") else 0.0
        
        fee = fc[0].number_input("Tổng phí giao dịch (đ)", min_value=0.0, value=float(est_fee) if auto else 0.0, step=1000.0, format="%.0f", disabled=auto)
        tax = fc[1].number_input("Tổng thuế TNCN bán 0.1% (đ)", min_value=0.0, value=float(est_tax) if auto else 0.0, step=1000.0, format="%.0f", disabled=auto)
        if auto and total_q > 0 and price > 0:
            st.caption(f"💡 Ước tính cho VPS: Phí ({cur_fee_rate*100:.2f}%) = **{est_fee:,.0f} đ** | Thuế bán ({cur_tax_rate*100:.1f}%) = **{est_tax:,.0f} đ**")
        note = st.text_input("Ghi chú")
        if st.form_submit_button("Lưu lệnh", type="primary"):
            try:
                if not sym or price <= 0:
                    raise ValueError("Nhập mã và giá")
                E.add_trade(conn, d, sym, side, price, alloc,
                            fee=None if auto else fee, tax=None if auto else tax, note=note)
                ok(f"Đã lưu lệnh {side} {sym}")
            except Exception as e:
                st.error(str(e))

    st.subheader("Lệnh gần đây")
    t = pd.read_sql_query(
        "SELECT t.date AS Ngày, o.name AS Người, t.symbol AS Mã, t.side AS Loại, t.qty AS KL, "
        "t.price AS Giá, t.fee AS Phí, t.tax AS Thuế, t.note AS 'Ghi chú' FROM trades t "
        "JOIN owners o ON o.id=t.owner_id ORDER BY t.date DESC, t.id DESC LIMIT 30", conn)
    st.dataframe(t, hide_index=True, use_container_width=True)


# ================================================================== CASH
elif page == "💵 Tiền & chuyển nhượng":
    st.title("💵 Tiền & chuyển nhượng giữa các thành viên")
    t1, t2, t3 = st.tabs(["Nạp / rút / khác", "Chuyển tiền giữa 2 người", "Chuyển cổ phiếu giữa 2 người"])
    with t1:
        with st.form("cash", clear_on_submit=True):
            c = st.columns(3)
            d = c[0].date_input("Ngày", date.today(), format="DD/MM/YYYY")
            o = c[1].selectbox("Người", OW_IDS, format_func=OW.get)
            typ = c[2].selectbox("Loại", ["DEPOSIT", "WITHDRAW", "INTEREST", "FEE"], format_func={
                "DEPOSIT": "Nạp tiền", "WITHDRAW": "Rút tiền", "INTEREST": "Lãi tiền gửi/ứng trước",
                "FEE": "Phí (lưu ký, SMS, lãi vay...)"}.get)
            amt = money_input("Số tiền (đ)", "camt")
            note = st.text_input("Ghi chú")
            if st.form_submit_button("Lưu", type="primary"):
                if amt <= 0:
                    st.error("Số tiền phải > 0")
                else:
                    E.add_cash(conn, d, o, typ, amt, note)
                    ok("Đã lưu")
        st.caption("💡 Phí chung của tài khoản (vd phí lưu ký) có thể ghi cho từng người theo tỷ lệ tài sản.")
    with t2:
        with st.form("xfer", clear_on_submit=True):
            c = st.columns(4)
            d = c[0].date_input("Ngày", date.today(), format="DD/MM/YYYY", key="xd")
            a = c[1].selectbox("Từ", OW_IDS, format_func=OW.get, key="xa")
            b = c[2].selectbox("Đến", OW_IDS, index=min(1, len(OW_IDS) - 1), format_func=OW.get, key="xb")
            with c[3]:
                amt = money_input("Số tiền (đ)", "xamt")
            note = st.text_input("Ghi chú", key="xn")
            if st.form_submit_button("Chuyển", type="primary"):
                try:
                    E.transfer_cash(conn, d, a, b, amt, note)
                    ok("Đã chuyển")
                except Exception as e:
                    st.error(str(e))
        st.caption("Dùng khi một người cho/mượn tiền người kia trong tài khoản. Không ảnh hưởng tổng tài khoản.")
    with t3:
        with st.form("sxfer", clear_on_submit=True):
            c = st.columns(5)
            d = c[0].date_input("Ngày", date.today(), format="DD/MM/YYYY", key="sd")
            a = c[1].selectbox("Từ", OW_IDS, format_func=OW.get, key="sa")
            b = c[2].selectbox("Đến", OW_IDS, index=min(1, len(OW_IDS) - 1), format_func=OW.get, key="sb")
            sym = c[3].text_input("Mã", key="ss").upper().strip()
            q = c[4].number_input("KL", min_value=0, step=100, key="sq")
            use_cost = st.checkbox("Chuyển theo giá vốn của người chuyển (không phát sinh lãi/lỗ)", True)
            pr = price_input("Hoặc giá chuyển nhượng", "sp")
            if st.form_submit_button("Chuyển", type="primary"):
                try:
                    E.transfer_shares(conn, d, a, b, sym, q, None if use_cost else pr)
                    ok("Đã chuyển cổ phiếu")
                except Exception as e:
                    st.error(str(e))


# ================================================================== DIVIDENDS
elif page == "🎁 Cổ tức":
    st.title("🎁 Nhận cổ tức & cổ phiếu thưởng")
    st.write("Chọn hình thức cổ tức công ty chi trả để hệ thống tự động phân bổ chính xác cho từng người:")
    
    div_mode = st.radio("Hình thức:", 
                        ["💵 Cổ tức bằng TIỀN MẶT", 
                         "📜 Cổ tức bằng CỔ PHIẾU (hoặc Cổ phiếu thưởng)",
                         "✍️ Nhập số lượng cổ phiếu thưởng trực tiếp"],
                        horizontal=True)
    
    if div_mode == "💵 Cổ tức bằng TIỀN MẶT":
        st.subheader("1. Cổ tức bằng tiền mặt")
        st.caption("Công ty trả tiền vào tài khoản (VD: trả 1.500 đ/cổ phiếu). Thuế TNCN cổ tức tiền là 5%.")
        
        c1, c2, c3 = st.columns(3)
        sym_cash = c1.text_input("Mã cổ phiếu", placeholder="VD: HPG, VHM").upper().strip()
        ex_date_cash = c2.date_input("Ngày Giao dịch không hưởng quyền (GDKHQ)", date.today(), format="DD/MM/YYYY")
        cash_per_share = c3.number_input("Số tiền trả trên 1 cổ phiếu (VNĐ)", min_value=0.0, step=100.0, value=1000.0,
                                         help="Ví dụ công ty thông báo tỷ lệ 10% tức là 1.000 đ/CP; tỷ lệ 15% là 1.500 đ/CP")
        
        pay_date = st.date_input("Ngày tiền về tài khoản (Ngày thanh toán)", date.today(), format="DD/MM/YYYY")
        
        if sym_cash:
            # Show live preview of who gets how much
            pos_prev = E.positions(conn, as_of=ex_date_cash.isoformat(), inclusive=False)
            pos_sym = pos_prev[(pos_prev.symbol == sym_cash) & (pos_prev.qty > 0)]
            if pos_sym.empty:
                st.warning(f"⚠️ Tại thời điểm ngày {ex_date_cash.strftime('%d/%m/%Y')}, không có ai sở hữu mã **{sym_cash}** trong sổ.")
            else:
                st.write("**Dự kiến phân bổ cổ tức tiền (sau thuế 5%):**")
                prev_data = []
                div_tax = db.get_setting(conn, "div_tax_rate")
                for r in pos_sym.itertuples():
                    gross = r.qty * cash_per_share
                    tax_amt = round(gross * div_tax)
                    net = gross - tax_amt
                    prev_data.append({
                        "Người nhận": OW.get(r.owner_id, ""),
                        "Số CP sở hữu": f"{r.qty:,.0f} CP",
                        "Tiền trước thuế": f"{gross:,.0f} đ",
                        "Thuế TNCN (5%)": f"-{tax_amt:,.0f} đ",
                        "Thực nhận vào ví": f"{net:,.0f} đ"
                    })
                st.dataframe(pd.DataFrame(prev_data), hide_index=True, use_container_width=True)
                
                if st.button("Xác nhận ghi nhận Cổ tức tiền", type="primary"):
                    try:
                        res = E.apply_corporate_action(conn, ex_date_cash, sym_cash, "CASH", cash_per_share, pay_date=pay_date)
                        ok("Đã ghi nhận cổ tức tiền vào tài khoản!")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
                        
    elif div_mode == "📜 Cổ tức bằng CỔ PHIẾU (hoặc Cổ phiếu thưởng)":
        st.subheader("2. Cổ tức bằng cổ phiếu theo tỷ lệ")
        st.caption("Công ty phát hành thêm cổ phiếu theo tỷ lệ % (VD: tỷ lệ 100:15 hoặc 15% tức là có 100 CP được thưởng 15 CP). Giá vốn cổ phiếu thưởng = 0 đ.")
        
        c1, c2, c3 = st.columns(3)
        sym_stock = c1.text_input("Mã cổ phiếu", placeholder="VD: HPG, MBB", key="stock_sym").upper().strip()
        ex_date_stock = c2.date_input("Ngày GDKHQ", date.today(), format="DD/MM/YYYY", key="stock_ex")
        ratio_pct = c3.number_input("Tỷ lệ chia (%)", min_value=0.0, step=1.0, value=10.0,
                                    help="Ví dụ chia tỷ lệ 100:10 thì nhập 10; chia tỷ lệ 100:15 thì nhập 15")
        
        if sym_stock:
            pos_prev = E.positions(conn, as_of=ex_date_stock.isoformat(), inclusive=False)
            pos_sym = pos_prev[(pos_prev.symbol == sym_stock) & (pos_prev.qty > 0)]
            if pos_sym.empty:
                st.warning(f"⚠️ Tại ngày {ex_date_stock.strftime('%d/%m/%Y')}, không có ai sở hữu mã **{sym_stock}**.")
            else:
                st.write("**Dự kiến số cổ phiếu thưởng nhận được:**")
                prev_data = []
                for r in pos_sym.itertuples():
                    new_shares = int(r.qty * ratio_pct / 100)
                    prev_data.append({
                        "Người nhận": OW.get(r.owner_id, ""),
                        "Số CP hiện có": f"{r.qty:,.0f} CP",
                        "Tỷ lệ": f"{ratio_pct:.1f}%",
                        "Số CP thưởng nhận thêm": f"+{new_shares:,.0f} CP",
                        "Tổng CP sau khi nhận": f"{r.qty + new_shares:,.0f} CP"
                    })
                st.dataframe(pd.DataFrame(prev_data), hide_index=True, use_container_width=True)
                
                if st.button("Xác nhận cộng Cổ phiếu thưởng", type="primary"):
                    try:
                        res = E.apply_corporate_action(conn, ex_date_stock, sym_stock, "STOCK", ratio_pct)
                        ok("Đã cộng thêm cổ phiếu thưởng vào danh mục!")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
                        
    else: # Nhập số lượng trực tiếp
        st.subheader("3. Nhập số lượng cổ phiếu thưởng cụ thể")
        st.caption("Dùng khi bạn thấy thông báo số CP lẻ hoặc CP thưởng đã về tài khoản và muốn cộng thẳng vào danh mục.")
        
        with st.form("direct_bonus"):
            c1, c2, c3, c4 = st.columns(4)
            d_bonus = c1.date_input("Ngày nhận", date.today(), format="DD/MM/YYYY")
            o_bonus = c2.selectbox("Người nhận", OW_IDS, format_func=OW.get)
            s_bonus = c3.text_input("Mã cổ phiếu").upper().strip()
            q_bonus = c4.number_input("Số lượng CP nhận thêm", min_value=1, step=1, value=10)
            note_bonus = st.text_input("Ghi chú", value="Cổ tức cổ phiếu / CP thưởng")
            
            if st.form_submit_button("Cộng cổ phiếu vào danh mục", type="primary"):
                if not s_bonus:
                    st.error("Vui lòng nhập mã cổ phiếu")
                else:
                    import uuid
                    gid = uuid.uuid4().hex[:12]
                    with E.tx(conn):
                        conn.execute(
                            "INSERT INTO trades(date, owner_id, symbol, side, qty, price, fee, tax, group_id, note) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (d_bonus.strftime("%Y-%m-%d"), o_bonus, s_bonus, "BONUS", float(q_bonus), 0, 0, 0, gid, note_bonus)
                        )
                    ok(f"Đã cộng +{q_bonus:,.0f} CP {s_bonus} cho {OW[o_bonus]}!")
                    st.rerun()

    st.write("---")
    st.subheader("Lịch sử các đợt chia cổ tức")
    ca = pd.read_sql_query("SELECT ex_date AS 'Ngày GDKHQ', symbol AS Mã, "
                           "CASE kind WHEN 'CASH' THEN 'Tiền mặt' ELSE 'Cổ phiếu' END AS 'Hình thức', "
                           "value AS 'Giá trị / Tỷ lệ', pay_date AS 'Ngày thanh toán' "
                           "FROM corporate_actions ORDER BY ex_date DESC", conn)
    if ca.empty:
        st.info("Chưa có đợt cổ tức nào được ghi nhận.")
    else:
        st.dataframe(ca, hide_index=True, use_container_width=True)


# ================================================================== OPENING
elif page == "🏁 Số dư đầu kỳ":
    st.title("🏁 Chốt số dư đầu kỳ")
    st.info("Vì trước đây tiền và cổ phiếu bị lẫn lộn, hãy chọn **một ngày mốc**, rồi chia tiền mặt và từng mã "
            "cổ phiếu hiện có cho từng người. Từ sau ngày đó, ghi mọi giao dịch qua app.")
    d = st.date_input("Ngày mốc", date.today(), format="DD/MM/YYYY")
    st.subheader("Tiền mặt")
    with st.form("open_cash"):
        cc = st.columns(len(OW))
        vals = {o: cc[i].number_input(f"Tiền mặt của {OW[o]} (đ)", min_value=0.0, step=1_000_000.0,
                                      format="%.0f", key=f"oc{o}") for i, o in enumerate(OW_IDS)}
        if st.form_submit_button("Lưu tiền mặt đầu kỳ"):
            for o, v in vals.items():
                if v > 0:
                    E.add_cash(conn, d, o, "OPENING", v, "Số dư đầu kỳ")
            ok("Đã lưu")
    st.subheader("Cổ phiếu")
    st.caption("Mỗi dòng: người, mã, khối lượng, giá vốn trung bình (đ). Có thể dán từ Excel.")
    blank = pd.DataFrame({"Người": pd.Series(dtype="str"), "Mã": pd.Series(dtype="str"),
                          "KL": pd.Series(dtype="float"), "Giá vốn TB": pd.Series(dtype="float")})
    ed = st.data_editor(blank, num_rows="dynamic", use_container_width=True, key="open_pos",
                        column_config={"Người": st.column_config.SelectboxColumn(options=list(OW.values()),
                                                                                 required=True)})
    if st.button("Lưu cổ phiếu đầu kỳ", type="primary"):
        name2id = {v: k for k, v in OW.items()}
        n = 0
        for r in ed.dropna(subset=["Người", "Mã", "KL"]).itertuples():
            gv = float(r._4 or 0)
            gv = gv * 1000 if 0 < gv < 1000 else gv
            E.add_opening_position(conn, d, name2id[r.Người], str(r.Mã), float(r.KL), gv)
            n += 1
        ok(f"Đã lưu {n} dòng")


# ================================================================== RECONCILE
elif page == "🔍 Đối soát":
    st.title("🔍 Đối soát với tài khoản thật")
    st.caption("Nhập số dư thật trên app công ty chứng khoán. Tổng của các thành viên phải khớp.")
    pos = E.positions(conn)
    ledger = pos[pos.qty > 0].groupby("symbol").qty.sum() if not pos.empty else pd.Series(dtype=float)
    cash_actual = st.number_input("Tiền mặt thực tế (đ)", min_value=-1e13, value=float(sum(E.cash_balances(conn).values())),
                                  step=1000.0, format="%.0f")
    df = pd.DataFrame({"Mã": ledger.index, "KL thực tế": ledger.values})
    ed = st.data_editor(df, num_rows="dynamic", use_container_width=True, hide_index=True)
    rec = E.reconcile(conn, cash_actual, dict(zip(ed["Mã"].astype(str).str.upper(), ed["KL thực tế"].fillna(0))))
    rec.columns = ["Hạng mục", "Sổ app", "Thực tế", "Chênh lệch", "Khớp"]
    st.dataframe(rec.style.format({"Sổ app": "{:,.0f}", "Thực tế": "{:,.0f}", "Chênh lệch": "{:+,.0f}"})
                 .map(lambda v: "" if v else "background-color:#fee2e2", subset=["Khớp"]),
                 hide_index=True, use_container_width=True)
    if rec["Khớp"].all():
        st.success("✅ Sổ sách khớp hoàn toàn với tài khoản.")
    else:
        st.error("❌ Có chênh lệch. Kiểm tra lệnh thiếu, phí, cổ tức, lãi tiền gửi... Có thể ghi giao dịch "
                 "'ADJUST' hoặc 'Phí/Lãi' cho người phù hợp.")


# ================================================================== JOURNAL
elif page == "📒 Sổ giao dịch":
    st.title("📒 Sổ giao dịch")
    t1, t2 = st.tabs(["Cổ phiếu", "Tiền"])
    with t1:
        t = pd.read_sql_query(
            "SELECT t.id, t.group_id, t.date, o.name AS owner, t.symbol, t.side, t.qty, t.price, t.fee, "
            "t.tax, t.note FROM trades t JOIN owners o ON o.id=t.owner_id ORDER BY t.date DESC, t.id DESC", conn)
        st.dataframe(t, hide_index=True, use_container_width=True)
    with t2:
        c = pd.read_sql_query(
            "SELECT c.id, c.group_id, c.date, o.name AS owner, c.type, c.amount, c.symbol, c.note "
            "FROM cash_tx c JOIN owners o ON o.id=c.owner_id ORDER BY c.date DESC, c.id DESC", conn)
        st.dataframe(c, hide_index=True, use_container_width=True)
    st.subheader("🗑️ Xoá & Reset dữ liệu")
    c1, c2, c3 = st.columns(3)
    with c1.form("delg"):
        g = st.text_input("group_id (xoá nhóm)")
        if st.form_submit_button("Xoá nhóm") and g:
            E.delete_group(conn, g.strip())
            ok("Đã xoá nhóm")
            st.rerun()
    with c2.form("delr"):
        tbl = st.selectbox("Bảng", ["trades", "cash_tx"])
        rid = st.number_input("id", min_value=0, step=1)
        if st.form_submit_button("Xoá dòng") and rid:
            E.delete_row(conn, tbl, int(rid))
            ok("Đã xoá dòng")
            st.rerun()
    with c3.expander("⚠️ Xoá toàn bộ dữ liệu"):
        st.write("Xoá sạch toàn bộ lệnh mua/bán và lịch sử tiền để import lại từ đầu.")
        if st.button("Xoá sạch sổ giao dịch", type="primary"):
            with db.tx(conn):
                conn.execute("DELETE FROM trades")
                conn.execute("DELETE FROM cash_tx")
                conn.execute("DELETE FROM corporate_actions")
                conn.execute("DELETE FROM snapshots")
            ok("Đã xoá toàn bộ dữ liệu giao dịch!")
            st.rerun()
            
    st.download_button("⬇️ Tải sổ lệnh (CSV)", t.to_csv(index=False).encode("utf-8-sig"), "trades.csv")
    
    with open(db.DEFAULT_DB, "rb") as f_db:
        st.download_button("💾 Sao lưu / Tải file Database (.db)", f_db.read(), "portfolio.db", mime="application/x-sqlite3")


# ================================================================== IMPORT
# ================================================================== IMPORT
elif page == "📥 Import":
    st.title("📥 Import lịch sử giao dịch")
    tab_sheet, tab_file = st.tabs(["🔗 Google Sheets", "📁 File CSV / Excel"])
    
    with tab_sheet:
        st.subheader("Nhập link Google Sheet")
        st.caption("💡 Hãy đảm bảo Google Sheet đã bật quyền **'Bất kỳ ai có đường liên kết đều có thể xem'** (Anyone with the link can view).")
        sheet_url = st.text_input("Đường link Google Sheet", placeholder="https://docs.google.com/spreadsheets/d/...")
        
        col_s1, col_s2 = st.columns(2)
        sheet_tab_name = col_s1.text_input("Tên Sheet / Tab (tuỳ chọn, để trống nếu lấy tab đầu tiên)")
        load_btn = st.button("Tải dữ liệu từ Google Sheet", type="primary")
        
        if load_btn and sheet_url:
            with st.spinner("Đang tải dữ liệu từ Google Sheet..."):
                try:
                    df_sheet = importer.read_google_sheet(sheet_url, sheet_name=sheet_tab_name if sheet_tab_name.strip() else None)
                    st.session_state["sheet_data"] = df_sheet
                    st.success(f"Tải thành công {len(df_sheet)} dòng dữ liệu!")
                except Exception as e:
                    st.error(str(e))
                    
        df_sheet = st.session_state.get("sheet_data")
        if df_sheet is not None and not df_sheet.empty:
            st.write("---")
            st.subheader("Phương thức Import")
            mode = st.radio("Chọn cách nhận diện dữ liệu:", 
                            ["🤖 Tự động nhận diện thông minh (Khuyên dùng - tự tách bảng, điền ngày, xử lý cổ tức)",
                             "🛠️ Tự ghép cột thủ công (Manual Mapping)"],
                            horizontal=False)
            
            if "Tự động" in mode:
                with st.spinner("Đang phân tích cấu trúc bảng..."):
                    df_trades_auto, df_cash_auto = importer.extract_trades_and_cash_from_sheet(df_sheet)
                    
                st.write(f"Đã phát hiện **{len(df_trades_auto)}** lệnh giao dịch cổ phiếu và **{len(df_cash_auto)}** giao dịch tiền/cổ tức.")
                
                t_tab1, t_tab2 = st.tabs([f"Cổ phiếu ({len(df_trades_auto)})", f"Tiền & Cổ tức ({len(df_cash_auto)})"])
                with t_tab1:
                    st.dataframe(df_trades_auto.head(15), use_container_width=True)
                with t_tab2:
                    st.dataframe(df_cash_auto, use_container_width=True)
                    
                init_cash = st.number_input("Tiền nạp ban đầu (nếu có, đ):", min_value=0.0, value=40_000_000.0, step=1_000_000.0, format="%.0f")
                target_owner = st.selectbox("Gán các giao dịch này cho ai?", list(OW.values()), key="auto_owner")
                owner_id = [k for k, v in OW.items() if v == target_owner][0]
                
                if st.button("🚀 Bắt đầu Import toàn bộ vào sổ", type="primary", key="btn_auto_import"):
                    n_trades = 0
                    n_cash = 0
                    errs = []
                    
                    if init_cash > 0:
                        first_date = df_trades_auto["date"].min() if not df_trades_auto.empty else "2025-03-30"
                        E.add_cash(conn, first_date, owner_id, "OPENING", init_cash, "Nạp tiền ban đầu")
                        n_cash += 1
                    
                    # Import trades
                    for i, r in df_trades_auto.iterrows():
                        try:
                            fee_val = None
                            tax_val = None
                            p = float(r["price"])
                            q = float(r["qty"])
                            eff = float(r["eff_price"]) if pd.notna(r.get("eff_price")) and r.get("eff_price") else None
                            
                            if eff and p > 0:
                                diff = abs(eff - p) * q
                                if r["side"] == "BUY":
                                    fee_val = round(diff)
                                else:
                                    auto_tax = round(q * p * float(E.get_setting(conn, "sell_tax_rate")))
                                    tax_val = auto_tax
                                    fee_val = max(0, round(diff - auto_tax))
                                    
                            if p == 0:
                                # Bonus share
                                import uuid
                                gid = uuid.uuid4().hex[:12]
                                with E.tx(conn):
                                    conn.execute(
                                        "INSERT INTO trades(date, owner_id, symbol, side, qty, price, fee, tax, group_id, note) "
                                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                                        (r["date"], owner_id, r["symbol"], "BONUS", q, 0, 0, 0, gid, r.get("note") or "Cổ tức CP / thưởng")
                                    )
                            else:
                                E.add_trade(conn, r["date"], r["symbol"], r["side"], p, {owner_id: q},
                                            fee=fee_val, tax=tax_val, note=r.get("note") or "import sheet", check_holdings=False)
                            n_trades += 1
                        except Exception as ex:
                            errs.append(f"Lệnh {r['symbol']} ({r['date']}): {ex}")
                            
                    # Import cash & dividends
                    for i, r in df_cash_auto.iterrows():
                        try:
                            amt = float(r["amount"])
                            E.add_cash(conn, r["date"], owner_id, r["type"], amt, note=r.get("note") or "", symbol=r.get("symbol"))
                            n_cash += 1
                        except Exception as ex:
                            errs.append(f"Tiền {r['date']}: {ex}")
                            
                    st.success(f"🎉 Hoàn tất! Đã nạp thành công **{n_trades}** lệnh cổ phiếu và **{n_cash}** giao dịch tiền/cổ tức cho **{target_owner}**!")
                    if errs:
                        with st.expander("Các lỗi ghi nhận:"):
                            for e in errs:
                                st.warning(e)
                                
            else:
                st.write("**Xem trước dữ liệu (5 dòng đầu):**")
                st.dataframe(df_sheet.head(5), use_container_width=True)
                
                st.subheader("Ghép cột dữ liệu (Column Mapping)")
                st.caption("💡 **Mẹo:** Nếu cột Khối lượng có dấu (+ là Mua, - là Bán), bạn có thể để **Cột Loại lệnh Mua/Bán là '-- Bỏ qua --'**, hệ thống sẽ tự nhận diện.")
                
                cols = ["-- Bỏ qua --"] + list(df_sheet.columns)
                
                def find_match(aliases):
                    for col in df_sheet.columns:
                        c_low = str(col).strip().lower()
                        if any(a in c_low for a in aliases):
                            return col
                    return "-- Bỏ qua --"
                    
                cm1, cm2, cm3 = st.columns(3)
                col_date = cm1.selectbox("Cột Ngày (*)", cols, index=cols.index(find_match(["ngày", "ngay", "date"])) if find_match(["ngày", "ngay", "date"]) in cols else 0)
                col_sym = cm2.selectbox("Cột Mã CK (*)", cols, index=cols.index(find_match(["mã", "ma", "symbol", "cp"])) if find_match(["mã", "ma", "symbol", "cp"]) in cols else 0)
                col_qty = cm3.selectbox("Cột Khối lượng (+ Mua, - Bán) (*)", cols, index=cols.index(find_match(["khối lượng", "khoi luong", "kl", "qty", "số lượng"])) if find_match(["khối lượng", "khoi luong", "kl", "qty", "số lượng"]) in cols else 0)
                
                cm4, cm5, cm6 = st.columns(3)
                col_price = cm4.selectbox("Cột Giá khớp (*)", cols, index=cols.index(find_match(["giá khớp", "gia khop", "giá", "gia", "price"])) if find_match(["giá khớp", "gia khop", "giá", "gia", "price"]) in cols else 0)
                col_eff_price = cm5.selectbox("Cột Giá sau thuế phí (Giá mua) (tuỳ chọn)", cols, index=cols.index(find_match(["giá mua", "gia mua", "sau thuế", "hiệu dụng"])) if find_match(["giá mua", "gia mua", "sau thuế", "hiệu dụng"]) in cols else 0)
                col_side = cm6.selectbox("Cột Loại lệnh (Mua/Bán - nếu có)", cols, index=cols.index(find_match(["loại", "loai", "mua/bán", "side", "lệnh"])) if find_match(["loại", "loai", "mua/bán", "side", "lệnh"]) in cols else 0)
                
                cm7, cm8, cm9 = st.columns(3)
                col_owner = cm7.selectbox("Cột Người sở hữu", cols, index=cols.index(find_match(["chủ", "người", "owner", "ai"])) if find_match(["chủ", "người", "owner", "ai"]) in cols else 0)
                col_fee = cm8.selectbox("Cột Phí (tuỳ chọn)", cols, index=cols.index(find_match(["phí", "phi", "fee"])) if find_match(["phí", "phi", "fee"]) in cols else 0)
                col_tax = cm9.selectbox("Cột Thuế (tuỳ chọn)", cols, index=cols.index(find_match(["thuế", "thue", "tax"])) if find_match(["thuế", "thue", "tax"]) in cols else 0)
                
                default_owner = None
                if col_owner == "-- Bỏ qua --":
                    default_owner = st.selectbox("Nếu Sheet không có cột người, gán toàn bộ lệnh cho ai?", list(OW.values()))
                    
                if st.button("Tiến hành Import từ Sheet", type="primary"):
                    mapping = {}
                    if col_date != "-- Bỏ qua --": mapping["date"] = col_date
                    if col_sym != "-- Bỏ qua --": mapping["symbol"] = col_sym
                    if col_side != "-- Bỏ qua --": mapping["side"] = col_side
                    if col_qty != "-- Bỏ qua --": mapping["qty"] = col_qty
                    if col_price != "-- Bỏ qua --": mapping["price"] = col_price
                    if col_eff_price != "-- Bỏ qua --": mapping["effective_price"] = col_eff_price
                    if col_owner != "-- Bỏ qua --": mapping["owner"] = col_owner
                    if col_fee != "-- Bỏ qua --": mapping["fee"] = col_fee
                    if col_tax != "-- Bỏ qua --": mapping["tax"] = col_tax
                    
                    try:
                        n, errs = importer.import_trades_mapped(conn, df_sheet, mapping, default_owner=default_owner)
                        st.success(f"🎉 Đã import thành công {n} lệnh vào sổ giao dịch!")
                        if errs:
                            with st.expander(f"Có {len(errs)} dòng bị bỏ qua hoặc lỗi"):
                                for e in errs:
                                    st.warning(e)
                    except Exception as ex:
                        st.error(f"Lỗi import: {ex}")

    with tab_file:
        st.write("Tải file CSV/Excel với các cột: `date, symbol, side, qty, price, fee, tax, owner` "
                 "(chấp nhận tiêu đề tiếng Việt: ngày, mã, loại (MUA/BÁN), KL, giá, phí, thuế, chủ).")
        st.download_button("⬇️ File mẫu", importer.TEMPLATE.to_csv(index=False).encode("utf-8-sig"), "mau_import.csv")
        f = st.file_uploader("Chọn file", type=["csv", "xlsx", "xls"], key="file_up")
        if f:
            raw = pd.read_csv(f) if f.name.endswith(".csv") else pd.read_excel(f)
            st.dataframe(raw.head(20), use_container_width=True)
            if st.button("Import File", type="primary"):
                try:
                    # Try direct import first, fallback to normalise
                    cols = {c: str(c).strip().lower() for c in raw.columns}
                    rename = {}
                    for orig, low in cols.items():
                        for key, names in importer.ALIASES.items():
                            if low in names:
                                rename[orig] = key
                    sub = raw.rename(columns=rename)
                    if "owner" not in sub.columns:
                        st.error("File chưa có cột Người sở hữu ('owner'/'chủ'/'người'). Vui lòng bổ sung hoặc dùng tab Google Sheet để chọn người mặc định.")
                    else:
                        n, errs = importer.import_trades_mapped(conn, raw, {k: k for k in sub.columns if k in ["date", "symbol", "side", "qty", "price", "fee", "tax", "owner"]})
                        st.success(f"Đã import {n} lệnh")
                        for e in errs:
                            st.warning(e)
                except Exception as e:
                    st.error(str(e))



# ================================================================== SETTINGS
elif page == "⚙️ Cài đặt":
    st.caption("Hiện tại đang cấu hình theo chuẩn CTCK **VPS**:")
    with st.form("settings"):
        c = st.columns(3)
        fee = c[0].number_input("Phí giao dịch VPS (%)", value=db.get_setting(conn, "fee_rate") * 100,
                                step=0.01, format="%.3f", help="VPS mặc định là 0.15% (bao gồm cả phí trả Sở giao dịch)")
        stax = c[1].number_input("Thuế bán TNCN (%)", value=db.get_setting(conn, "sell_tax_rate") * 100,
                                 step=0.01, format="%.3f", help="Thuế TNCN cố định theo quy định nhà nước là 0.1% giá trị bán")
        dtax = c[2].number_input("Thuế cổ tức tiền (%)", value=db.get_setting(conn, "div_tax_rate") * 100,
                                 step=0.5, format="%.2f", help="Thuế TNCN khấu trừ cổ tức tiền mặt là 5%")
        if st.form_submit_button("Lưu cài đặt", type="primary"):
            db.set_setting(conn, "fee_rate", fee / 100)
            db.set_setting(conn, "sell_tax_rate", stax / 100)
            db.set_setting(conn, "div_tax_rate", dtax / 100)
            ok("Đã lưu cài đặt thuế phí!")
    st.subheader("Thành viên & Vốn góp ròng")
    st.caption("Có thể cố định con số Vốn góp ròng cho từng thành viên để đối soát chính xác theo mong muốn.")
    with st.form("custom_contrib"):
        cc_cols = st.columns(len(OW))
        contrib_inputs = {}
        for i, o in enumerate(OW_IDS):
            cur_custom = db.get_setting(conn, f"custom_net_contrib_{o}")
            val = float(cur_custom) if cur_custom is not None else float(E.net_contributions(conn).get(o, 0.0))
            contrib_inputs[o] = cc_cols[i].number_input(f"Vốn góp ròng của {OW[o]} (đ)", min_value=0.0, value=val, step=1_000_000.0, format="%.0f")
        if st.form_submit_button("Lưu vốn góp ròng"):
            for o, v in contrib_inputs.items():
                db.set_setting(conn, f"custom_net_contrib_{o}", str(v))
            ok("Đã cập nhật vốn góp ròng!")
            st.rerun()

    with st.form("addowner", clear_on_submit=True):
        n = st.text_input("Thêm thành viên")
        if st.form_submit_button("Thêm") and n.strip():
            E.add_owner(conn, n)
            st.rerun()
    st.subheader("Giá thủ công")
    st.caption("Dùng khi vnstock không lấy được giá (mã mới, mất mạng...).")
    with st.form("mprice", clear_on_submit=True):
        c = st.columns(2)
        s = c[0].text_input("Mã").upper().strip()
        with c[1]:
            p = price_input("Giá", "mp")
        if st.form_submit_button("Lưu giá") and s and p > 0:
            P.set_manual_price(conn, s, p)
            ok(f"{s} = {p:,.0f}đ")
    st.caption(f"Dữ liệu lưu tại `{db.DEFAULT_DB}` — hãy sao lưu file này định kỳ.")
