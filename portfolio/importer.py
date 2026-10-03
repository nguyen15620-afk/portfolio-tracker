"""Generic CSV/Excel & Google Sheet importer for trades and transactions."""
from __future__ import annotations

import io
import re
import urllib.parse
import pandas as pd
import requests

from . import engine as E

ALIASES = {
    "date": ["date", "ngay", "ngày", "ngay gd", "ngày gd", "ngày giao dịch"],
    "symbol": ["symbol", "ma", "mã", "ma ck", "mã ck", "mã cp"],
    "side": ["side", "loai", "loại", "mua/ban", "mua/bán", "loại lệnh"],
    "qty": ["qty", "kl", "khoi luong", "khối lượng", "kl khớp"],
    "price": ["price", "gia", "giá", "giá khớp"],
    "fee": ["fee", "phi", "phí"],
    "tax": ["tax", "thue", "thuế"],
    "owner": ["owner", "chu", "chủ", "người", "nguoi"],
}
SIDE_MAP = {"MUA": "BUY", "M": "BUY", "BUY": "BUY", "B": "BUY",
            "BAN": "SELL", "BÁN": "SELL", "SELL": "SELL", "S": "SELL"}

TEMPLATE = pd.DataFrame([
    {"date": "2026-10-01", "symbol": "FPT", "side": "BUY", "qty": 100, "price": 62700,
     "fee": "", "tax": "", "owner": "Tôi"},
    {"date": "2026-10-01", "symbol": "HPG", "side": "SELL", "qty": 500, "price": 19.9,
     "fee": "", "tax": "", "owner": "Mẹ"},
])


def parse_google_sheet_url(url: str, sheet_name: str | None = None, gid: str | None = None) -> tuple[str, str]:
    """Extract (sheet_id, gid) from Google Sheet URL."""
    url = url.strip()
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise ValueError("Link Google Sheets không hợp lệ (không tìm thấy Spreadsheet ID)")
    sheet_id = match.group(1)
    
    if not gid:
        gid_match = re.search(r"[#&?]gid=([0-9]+)", url)
        if gid_match:
            gid = gid_match.group(1)
            
    return sheet_id, gid or "0"


def read_google_sheet(url: str, sheet_name: str | None = None, gid: str | None = None) -> pd.DataFrame:
    """Fetch Google Sheet using gviz endpoint for reliable CSV extraction."""
    sheet_id, target_gid = parse_google_sheet_url(url, sheet_name=sheet_name, gid=gid)
    
    gviz_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={target_gid}"
    if sheet_name:
        gviz_url += f"&sheet={urllib.parse.quote(sheet_name)}"
        
    try:
        resp = requests.get(gviz_url, timeout=15)
        if resp.status_code == 200:
            df = pd.read_csv(io.StringIO(resp.text), header=None)
            return df
    except Exception:
        pass
        
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={target_gid}"
    try:
        df = pd.read_csv(export_url, header=None)
        return df
    except Exception as e:
        raise RuntimeError(
            f"Không thể đọc Google Sheet: {e}. Vui lòng đảm bảo Sheet đã được bật chia sẻ "
            f"('Bất kỳ ai có đường liên kết đều có thể xem' / 'Anyone with the link can view')."
        )


def _clean_numeric(series: pd.Series) -> pd.Series:
    """Clean formatted numbers like '1,000,000', '1.000.000', '(500)' into floats."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    s = series.astype(str).str.strip()
    s = s.str.replace(" ", "").str.replace("đ", "").str.replace("₫", "").str.replace("VND", "", case=False)
    s = s.str.replace(r"^\((.+)\)$", r"-\1", regex=True)
    s = s.str.replace(",", "")
    return pd.to_numeric(s, errors="coerce")


def extract_trades_and_cash_from_sheet(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Smartly detects the trade detail sub-table, forward-fills merged dates,
    and separates stock trades from cash movements/dividends."""
    detail_col = None
    header_row = None
    
    for r in range(min(10, len(raw_df))):
        for c in range(len(raw_df.columns)):
            val = str(raw_df.iloc[r, c]).strip().lower()
            if val in ("chi tiết", "mã", "mã ck") and c >= 5:
                detail_col = c if val != "chi tiết" else c - 1
                header_row = r
                break
        if detail_col is not None:
            break
            
    if detail_col is None:
        for c in range(len(raw_df.columns)):
            sample = raw_df.iloc[1:10, c].astype(str)
            if sample.str.contains(r"\d{1,2}/\d{1,2}/\d{4}").any():
                detail_col = c
                header_row = 1
                break
                
    if detail_col is None:
        detail_col = 0
        header_row = 0

    if detail_col > 0:
        prev_sample = raw_df.iloc[header_row:header_row+15, detail_col - 1].astype(str)
        if prev_sample.str.contains(r"\d{1,2}/\d{1,2}/\d{4}").any():
            detail_col -= 1

    sub = raw_df.iloc[header_row + 1:, detail_col:detail_col + 6].copy()
    sub.columns = ["date", "symbol", "qty", "price", "eff_price", "value"][:len(sub.columns)]
    
    last_date = None
    trades = []
    cash_moves = []
    
    for idx, r in sub.iterrows():
        raw_date = str(r["date"]).strip() if "date" in r and pd.notna(r["date"]) and str(r["date"]).strip() not in ("", "nan") else None
        if raw_date and ("/" in raw_date or "-" in raw_date):
            last_date = raw_date
        date_val = last_date
        
        sym = str(r["symbol"]).strip().upper() if "symbol" in r and pd.notna(r["symbol"]) and str(r["symbol"]).strip() not in ("", "nan", "MÃ", "NONE") else None
        
        qty_str = str(r["qty"]).replace(",", "").strip() if "qty" in r and pd.notna(r["qty"]) else ""
        try:
            qty = float(qty_str) if qty_str and qty_str != "nan" else None
        except:
            qty = None
            
        price_str = str(r["price"]).replace(",", ".").strip() if "price" in r and pd.notna(r["price"]) else ""
        try:
            price = float(price_str) if price_str and price_str != "nan" else None
        except:
            price = None
            
        eff_str = str(r["eff_price"]).replace("đ", "").replace("₫", "").replace(".", "").replace(",", "").replace(" ", "").strip() if "eff_price" in r and pd.notna(r["eff_price"]) else ""
        try:
            eff_price = float(eff_str) if eff_str and eff_str != "nan" else None
        except:
            eff_price = None
            
        val_str = str(r["value"]).replace("đ", "").replace("₫", "").replace(".", "").replace(",", "").replace(" ", "").strip() if "value" in r and pd.notna(r["value"]) else ""
        try:
            val = float(val_str) if val_str and val_str != "nan" else 0.0
        except:
            val = 0.0
            
        if date_val:
            try:
                date_iso = pd.to_datetime(date_val, dayfirst=True).strftime("%Y-%m-%d")
            except Exception:
                date_iso = date_val
        else:
            date_iso = date_val
            
        if sym and qty is not None and qty != 0:
            p = (price * 1000 if 0 < price < 1000 else price) if price else 0
            trades.append({
                "date": date_iso,
                "symbol": sym,
                "side": "BUY" if qty > 0 else "SELL",
                "qty": abs(qty),
                "price": p,
                "eff_price": eff_price,
                "note": "Cổ phiếu thưởng/cổ tức CP" if p == 0 else ""
            })
        elif val != 0:
            # Negative amount without symbol on user sheet represents cash deposited from bank (e.g. -10tr, -15tr, -3tr)
            if sym:
                typ = "DIVIDEND"
                amt = abs(val)
                note = f"Cổ tức tiền {sym}"
            else:
                typ = "DEPOSIT" if val < 0 else "WITHDRAW"
                amt = abs(val)
                note = "Nạp tiền vào tài khoản" if val < 0 else "Rút tiền"
                
            cash_moves.append({
                "date": date_iso,
                "symbol": sym,
                "type": typ,
                "amount": amt,
                "note": note
            })
            
    df_trades = pd.DataFrame(trades)
    df_cash = pd.DataFrame(cash_moves)
    if not df_trades.empty:
        df_trades = df_trades.sort_values(["date"]).reset_index(drop=True)
    if not df_cash.empty:
        df_cash = df_cash.sort_values(["date"]).reset_index(drop=True)
    return df_trades, df_cash


def import_trades_mapped(conn, df: pd.DataFrame, col_map: dict[str, str], default_owner: str | None = None) -> tuple[int, list[str]]:
    """Import trades using an explicit column mapping: {target_field: actual_df_col}.
    
    Supports forward-filling dates and signed quantities (positive = BUY, negative = SELL).
    """
    owners = {v.lower(): k for k, v in E.list_owners(conn).items()}
    renamed = {}
    for target, actual in col_map.items():
        if actual and actual in df.columns:
            renamed[actual] = target
    sub = df.rename(columns=renamed)
    
    missing = {"date", "symbol", "qty", "price"} - set(sub.columns)
    if missing:
        raise ValueError(f"Chưa chọn cột tương ứng cho: {', '.join(sorted(missing))}")
        
    sub = sub.copy()
    
    # Forward fill empty dates
    sub["date"] = sub["date"].replace("", float("nan")).ffill()
    sub["date"] = pd.to_datetime(sub["date"], dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d")
    sub["symbol"] = sub["symbol"].astype(str).str.upper().str.strip()
    
    raw_qty = _clean_numeric(sub["qty"])
    
    if "side" in sub.columns and sub["side"].notna().any():
        sub["side"] = sub["side"].astype(str).str.upper().str.strip().map(SIDE_MAP)
        sub.loc[sub["side"].isna() & (raw_qty > 0), "side"] = "BUY"
        sub.loc[sub["side"].isna() & (raw_qty < 0), "side"] = "SELL"
    else:
        sub["side"] = raw_qty.apply(lambda q: "BUY" if q > 0 else ("SELL" if q < 0 else None))
        
    sub["qty"] = raw_qty.abs()
    
    raw_price = _clean_numeric(sub["price"])
    raw_price = raw_price.apply(lambda p: p * 1000 if 0 < p < 1000 else p)
    sub["price"] = raw_price
    
    for c in ("fee", "tax", "value", "effective_price"):
        if c in sub.columns:
            sub[c] = _clean_numeric(sub[c])
        else:
            sub[c] = float("nan")
            
    if "owner" in sub.columns:
        sub["owner"] = sub["owner"].astype(str).str.strip()
    else:
        if not default_owner:
            raise ValueError("Sheet không có cột Người sở hữu, bạn vui lòng chọn người sở hữu mặc định.")
        sub["owner"] = default_owner
        
    sub = sub.sort_values("date", kind="stable")
    n, errors = 0, []
    for i, r in sub.iterrows():
        try:
            if pd.isna(r.date) or pd.isna(r.side) or pd.isna(r.qty) or pd.isna(r.price) or r.qty <= 0:
                continue
            oid = owners.get(str(r.owner).lower())
            if oid is None:
                raise ValueError(f"Không tìm thấy người dùng '{r.owner}' trong hệ thống")
                
            fee_val = None if pd.isna(r.fee) else float(r.fee)
            tax_val = None if pd.isna(r.tax) else float(r.tax)
            
            if fee_val is None and pd.notna(r.effective_price) and r.effective_price > 0:
                diff = abs(r.effective_price - r.price) * r.qty
                if r.side == "BUY":
                    fee_val = round(diff)
                elif r.side == "SELL":
                    gross = r.qty * r.price
                    auto_tax = round(gross * float(E.get_setting(conn, "sell_tax_rate")))
                    tax_val = auto_tax
                    fee_val = max(0, round(diff - auto_tax))
            
            # If price is 0, record as bonus shares / stock dividend
            if r.price == 0:
                import uuid
                gid = uuid.uuid4().hex[:12]
                with E.tx(conn):
                    conn.execute(
                        "INSERT INTO trades(date, owner_id, symbol, side, qty, price, fee, tax, group_id, note) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (r.date, oid, r.symbol, "BONUS", float(r.qty), 0, 0, 0, gid, "Cổ tức CP / thưởng")
                    )
            else:
                E.add_trade(conn, r.date, r.symbol, r.side, float(r.price), {oid: float(r.qty)},
                            fee=fee_val, tax=tax_val, note="import sheet", check_holdings=False)
            n += 1
        except Exception as e:
            errors.append(f"Dòng {i + 2}: {e}")
    return n, errors


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c: str(c).strip().lower() for c in df.columns}
    rename = {}
    for orig, low in cols.items():
        for key, names in ALIASES.items():
            if low in names:
                rename[orig] = key
    df = df.rename(columns=rename)
    missing = {"date", "symbol", "side", "qty", "price", "owner"} - set(df.columns)
    if missing:
        raise ValueError(f"Thiếu cột: {', '.join(sorted(missing))}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d")
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["side"] = df["side"].astype(str).str.upper().str.strip().map(SIDE_MAP)
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df.loc[df.price < 1000, "price"] *= 1000
    for c in ("fee", "tax"):
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else float("nan")
    df["owner"] = df["owner"].astype(str).str.strip()
    return df
