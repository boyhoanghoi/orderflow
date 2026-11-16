# OrderFlow API

Dự án cá nhân luyện Backend Python và nghiệp vụ đơn hàng–tồn kho, dùng FastAPI, SQLAlchemy, SQLite, JWT và Argon2. Bản 1.1 bổ sung lịch sử biến động kho và báo cáo đối soát cho hướng ERP hỗ trợ/kiểm thử.

Xem hướng dẫn chạy, kiến trúc và kiểm thử trong [README tiếng Anh](README.md). Cài Python 3.12+ và `pip install -r requirements-dev.txt`, sau đó chạy `python scripts/demo.py` để thấy quy trình đặt hàng, gửi lại request và hủy đơn trên database tạm. Lệnh này không cần tài khoản thật và không sửa database hiện tại.

- Đặt hàng: kiểm tra tồn kho và ghi biến động trong cùng transaction; lỗi một dòng thì rollback toàn bộ.
- Gửi lại cùng `Idempotency-Key` và nội dung: trả lại đơn cũ, không trừ kho lần nữa.
- Hủy đơn chưa giao: hoàn kho một lần; giao hàng không trừ kho lần hai.
- Báo cáo admin: lịch sử theo sản phẩm/người thao tác/đơn hàng và chênh lệch giữa tồn thực tế với tổng lịch sử.
- Có kiểm thử tự động, CI, báo cáo JUnit và demo tái lập.

Đây là module demo để học và phỏng vấn, chưa phải ERP hoàn chỉnh hay kinh nghiệm triển khai cho khách hàng. Xem [giới hạn và hướng hỗ trợ](docs/inventory-support.md).
