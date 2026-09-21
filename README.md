# OrderFlow API

Backend cho quy trình đặt hàng, quản lý tồn kho và xử lý trạng thái đơn. Dự án dùng FastAPI, SQLAlchemy, SQLite cho demo, Argon2 để băm mật khẩu và JWT để xác thực.

## Nghiệp vụ chính

- Khách đăng ký/đăng nhập, xem và tìm sản phẩm, tạo và xem đơn của mình.
- Quản trị viên thêm sản phẩm, điều chỉnh tồn kho và xác nhận giao hàng.
- Mỗi lần đặt hàng trừ tồn kho có điều kiện trong cùng transaction. Nếu một sản phẩm hết hàng, toàn bộ đơn được rollback.
- Header `Idempotency-Key` giúp gửi lại yêu cầu đặt hàng mà không tạo thêm đơn hoặc trừ kho lần hai.
- Hủy đơn khi chưa giao sẽ hoàn tồn kho. Giá bán được lưu snapshot trong từng dòng đơn.
- Phân trang, kiểm tra dữ liệu đầu vào, mã lỗi HTTP và OpenAPI tại `/docs`.

## Chạy local

Yêu cầu Python 3.13. Trong thư mục dự án:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env  # Trên Windows dùng: copy .env.example .env
```

Sửa `JWT_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` trong `.env`, sau đó nạp biến môi trường hoặc dùng Docker Compose (`docker compose up --build`). Với shell local:

```bash
uvicorn app.main:app --reload --env-file .env
```

Mở `http://127.0.0.1:8000/docs`. Dữ liệu demo được lưu vào file SQLite. Không dùng khóa bí mật mẫu khi triển khai thực tế.

## Thử API

1. `POST /auth/login` với tài khoản admin trong `.env` để lấy token.
2. `POST /products` tạo sản phẩm, gửi `Authorization: Bearer <token>`.
3. `POST /auth/register` tạo khách; `POST /auth/login` lấy token khách.
4. `POST /orders`, gửi token khách và `Idempotency-Key: checkout-001`:

```json
{"items": [{"product_id": 1, "quantity": 2}]}
```

5. Gửi lại cùng key để thấy `id` không đổi. `POST /orders/1/cancel` để hoàn tồn kho.

## Kiểm thử

```bash
pytest -q
```

Test bao phủ đặt đơn, gửi lại request, hết hàng và rollback, hoàn kho, giao hàng, phân quyền và truy cập chéo tài khoản. Mỗi test dùng database SQLite riêng.

## Giới hạn bản demo

`create_all` chỉ tạo schema ban đầu; khi triển khai lâu dài cần migration. SQLite phù hợp demo một tiến trình; tải ghi đồng thời lớn nên dùng PostgreSQL và thêm kiểm thử concurrency trên database đó. Chưa có thanh toán hay tích hợp hãng vận chuyển.
