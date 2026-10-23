# Đọc danh sách lượt từ ảnh

Ứng dụng Streamlit chạy OCR cục bộ bằng Tesseract. Bộ nhận diện tự phân loại hai layout lịch sử cược: `sc8882` và `alo789`. Source code có thể đưa lên GitHub; Docker image được build trực tiếp trên máy chạy ứng dụng và không cần push lên Docker Hub hoặc GitHub Container Registry.

Với ảnh `alo789`, ứng dụng ánh xạ cột **Cược** vào `bet_amount`, cột **Lời** vào `net_result`, ghép các mã trong cột **Giao Dịch** vào `ticket_id`, và lưu `source_type=alo789`. Vì layout này không có cột cược hợp lệ/trạng thái thanh toán, hai trường đó được để trống.

## Chạy trên VPS bằng Docker Compose

VPS cần có Git, Docker và Docker Compose plugin.

```bash
cd /opt
git clone git@github.com:USERNAME/REPOSITORY.git bet-image-reader
cd bet-image-reader

cp .env.example .env
mkdir -p data
docker compose up -d --build
```

Kiểm tra container:

```bash
docker compose ps
docker compose logs -f bet-image-reader
```

Khi cập nhật source code:

```bash
cd /opt/bet-image-reader
git pull
docker compose up -d --build --remove-orphans
```

Dừng ứng dụng:

```bash
docker compose down
```

Image local có tên `bet-image-reader:local`. Nó chỉ tồn tại trong Docker của VPS và không được đẩy lên registry.

## Cấu hình truy cập từ Internet

Mặc định `.env.example` dùng:

```dotenv
APP_BIND_ADDRESS=127.0.0.1
APP_PORT=8501
DATA_DIR=./data
```

Giữ `127.0.0.1` nếu dùng Nginx hoặc Caddy làm reverse proxy. Nếu muốn truy cập trực tiếp bằng IP VPS, đặt trong file `.env`:

```dotenv
APP_BIND_ADDRESS=0.0.0.0
APP_PORT=8501
```

Ứng dụng hiện chưa có đăng nhập, vì vậy nên dùng reverse proxy, HTTPS và cơ chế bảo vệ truy cập trước khi mở ra Internet.

## Chạy qua Portainer

### Portainer Docker Standalone

Build image trên VPS trước:

```bash
cd /opt/bet-image-reader
docker build -t bet-image-reader:local .
```

Trong Portainer chọn **Stacks → Add stack → Web editor**, rồi dùng nội dung `docker-compose.portainer.yml`. Portainer phải quản lý đúng Docker Environment của VPS để nhìn thấy image local.

Không chạy thêm `docker compose up` cho cùng stack nếu đã deploy stack đó từ Portainer.

### Portainer Docker Swarm

Build image trên VPS:

```bash
cd /opt/bet-image-reader
docker build -t bet-image-reader:local .
```

Trong Portainer tạo Stack bằng file `docker-stack.yml`. Nếu dữ liệu cần nằm ở đường dẫn cụ thể trên VPS, đặt biến stack:

```dotenv
DATA_DIR=/opt/bet-image-reader/data
```

File Stack giữ một replica vì database SQLite không phù hợp để nhiều container ghi đồng thời. Nếu Swarm có nhiều node, image local phải tồn tại trên node được chọn; với một VPS duy nhất thì không có vấn đề này.

## Chạy trên Windows

1. Cài và khởi động Docker Desktop.
2. Nhấp đúp `start.bat`.
3. Mở <http://localhost:8501>.
4. Nhấp đúp `stop.bat` để dừng ứng dụng.

Database được lưu trong `data/records.sqlite3` và vẫn còn sau khi container dừng hoặc được tạo lại. Thư mục `data` và file `.env` không được commit lên GitHub.

## Sao lưu dữ liệu

Trên VPS, sao lưu file database:

```bash
cp /opt/bet-image-reader/data/records.sqlite3 \
   /opt/bet-image-reader/data/records.sqlite3.backup
```

Ảnh gốc không được lưu trong ứng dụng. OCR có thể nhầm ID vé hoặc số tiền, nên cần kiểm tra những dòng được đánh dấu **Cần kiểm tra?**.
