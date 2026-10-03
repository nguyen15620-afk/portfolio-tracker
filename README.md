# Danh mục gia đình — theo dõi lời/lỗ & tài sản ròng từng người trên 1 tài khoản chung

## Chạy
- Nhấp đúp `run.bat`, hoặc: `& "$HOME\.venv\Scripts\python.exe" -m streamlit run app.py`
- Cài lại thư viện: `pip install --extra-index-url https://vnstocks.com/api/simple -r requirements.txt`

## Quy trình sử dụng
1. **Lần đầu**: nhập tên thành viên (mặc định *Tôi*, *Mẹ*).
2. **🏁 Số dư đầu kỳ**: chọn ngày mốc, chia tiền mặt và từng mã CP đang có cho từng người (kèm giá vốn).
3. **🔍 Đối soát**: kiểm tra tổng các thành viên khớp với số dư thật trên app CTCK.
4. Hằng ngày: ghi **lệnh khớp** (chia KL cho từng người, phí/thuế tự tính), **nạp/rút**, **cổ tức**.
5. **📊 Tổng quan**: bấm *Cập nhật giá* để xem NAV, lãi/lỗ đã chốt & tạm tính của từng người.

## Cách tính
- Giá vốn bình quân gia quyền; phí mua cộng vào giá vốn; phí + thuế bán trừ vào lãi đã chốt.
- **Lãi/lỗ tổng = NAV − Vốn góp ròng** (vốn góp = đầu kỳ + nạp − rút ± chuyển giữa thành viên).
- Cổ tức chia theo số CP mỗi người giữ trước ngày GDKHQ; cổ tức tiền trừ 5% thuế.
- Tiền mặt âm của một người = đang dùng tiền của người khác/margin → nên ghi *chuyển tiền*.

## Cấu trúc
- `portfolio/db.py` — schema SQLite (`data/portfolio.db`, hãy sao lưu file này)
- `portfolio/engine.py` — ghi sổ, tính giá vốn, P&L, NAV, đối soát
- `portfolio/prices.py` — giá từ vnstock + cache + giá thủ công
- `portfolio/importer.py` — import CSV/Excel lịch sử khớp lệnh
- `tests/` — `python -m pytest`
