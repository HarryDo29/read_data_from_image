# Đọc danh sách lượt từ ảnh

Bản thử dành cho máy tính Windows, chạy trong Docker Desktop. Giao diện mở trong trình duyệt tại `http://localhost:8501`; ảnh được OCR trong container cục bộ.

## Chạy trên Windows

1. Cài và khởi động Docker Desktop.
2. Giải nén gói dự án vào một thư mục.
3. Nhấp đúp `start.bat`. Lần đầu Docker sẽ tải các thành phần cần thiết và dựng ứng dụng; bước này có thể mất vài phút.
4. Trình duyệt mở tại <http://localhost:8501>.
5. Tải lên một ảnh dài hoặc chọn nhiều ảnh cùng lúc, rồi bấm **Đọc và lưu vào danh sách**.
6. Các lượt mới được nối vào danh sách đã lưu; lượt trùng sẽ tự được bỏ qua. Kiểm tra/sửa bảng, đánh dấu cột **Chọn xóa** rồi bấm **Xóa dòng đã chọn** để xóa lượt không đúng. Bấm **Xóa hết danh sách đã lưu** rồi xác nhận nếu muốn xóa toàn bộ lượt. Bấm nút lưu để giữ các chỉnh sửa trong bảng, rồi tải CSV.

Đóng ứng dụng bằng cách nhấp đúp `stop.bat`. Dữ liệu đã lưu trong `data/records.sqlite3` vẫn được giữ lại. Có thể chạy thủ công bằng PowerShell với `docker compose up --build -d`, rồi dừng bằng `docker compose down`.

Mỗi dòng có cột **Ảnh nguồn** và **Lưu lúc** để đối chiếu. Ứng dụng bỏ qua lượt có ID vé đã lưu hoặc bị lặp trong ảnh đang đọc. Nếu sửa bảng, bấm lưu để giữ thay đổi. Nút xóa áp dụng ngay; hãy lưu các chỉnh sửa khác trước nếu muốn giữ chúng. CSV dùng dấu chấm phẩy để chia cột và dấu phẩy cho số thập phân, phù hợp khi mở bằng Excel theo thiết lập vùng Việt Nam.

Docker image cài sẵn Tesseract cùng gói ngôn ngữ tiếng Việt và tiếng Anh. Trên Windows không cần cài Python hay Tesseract riêng. Nếu sửa mã nguồn, chạy lại `docker compose up --build` để tạo image mới.

Kết quả OCR được lưu cục bộ trong `data/records.sqlite3` trên máy và giữ lại sau khi dừng/khởi động lại Docker. Xóa ảnh khỏi vùng tải lên chỉ xóa lựa chọn ảnh, không xóa danh sách đã lưu. Ảnh gốc không được lưu. Có thể sao lưu file cơ sở dữ liệu này để giữ dữ liệu.

Đây là bản thử cho bố cục ảnh giống ảnh mẫu. OCR có thể nhầm ID vé hoặc số tiền, vì vậy hãy đối chiếu những dòng có nhãn **Cần kiểm tra?** trước khi dùng thống kê.
