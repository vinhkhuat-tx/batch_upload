# Hướng dẫn: Tạo file Excel để Import Glossary

Hướng dẫn này mô tả định dạng file Excel (`.xlsx`) dùng cho script `upload_glossary.py` để import **Glossary Terms** vào OpenMetadata.

---

## 1. Định dạng file

- **Loại file:** `.xlsx` (Excel)
- **Sheet:** Dữ liệu phải nằm ở **sheet đầu tiên**
- **Dòng tiêu đề:** Dòng 1 phải chứa đúng tên cột như bảng bên dưới
- Có thể chia dữ liệu thành **nhiều file** (cấu hình bằng danh sách key S3 cách nhau bởi dấu phẩy)

---

## 2. Danh sách cột

File Excel **bắt buộc** phải có đủ 16 cột bên dưới. Cột đánh dấu ★ không được để trống.

| # | Tên cột           | Bắt buộc | Mô tả                                                                      |
|---|--------------------|----------|-----------------------------------------------------------------------------|
| 1 | **Glossary** ★     | Có       | Tên glossary (phải đã tồn tại trong OpenMetadata)                           |
| 2 | **Parent**         | Không    | FQN của glossary term cha. Dùng `_` làm dấu phân cách (VD: `Business_Revenue`) |
| 3 | **Term Name** ★    | Có       | Tên duy nhất của glossary term                                              |
| 4 | **Display Name** ★ | Có       | Tên hiển thị                                                                |
| 5 | **Description** ★  | Có       | Mô tả thuật ngữ (hỗ trợ Markdown)                                          |
| 6 | **Synonyms**       | Không    | Danh sách từ đồng nghĩa, cách nhau bởi `;` (VD: `Income;Earnings;Profit`)  |
| 7 | **Related Terms**  | Không    | FQN các term liên quan, cách nhau bởi `;`. Dùng `_` làm dấu phân cách      |
| 8 | **Owner**          | Không    | Username chủ sở hữu (phải tồn tại trong OpenMetadata)                      |
| 9 | **Reviewers**      | Không    | Username người review (phải tồn tại trong OpenMetadata)                     |
| 10| **References**     | Không    | Danh sách URL tham khảo, cách nhau bởi `;` (URL phải hợp lệ)              |
| 11| **Tags**           | Không    | FQN các tag, cách nhau bởi `;`. Dùng `_` làm dấu phân cách (VD: `PII_Sensitive`) |
| 12| **Service Name**   | Không*   | Tên database service trong OpenMetadata                                     |
| 13| **Database Name**  | Không*   | Tên database                                                                |
| 14| **Schema Name**    | Không*   | Tên schema                                                                  |
| 15| **Table Name**     | Không*   | Tên table                                                                   |
| 16| **Column Name**    | Không*   | Tên column cần gắn glossary term vào                                        |

> **\*** Cột 12–16 (thông tin DB) là tùy chọn, nhưng nếu điền **bất kỳ** cột nào thì phải điền **đủ cả 5**. Script sẽ gắn glossary term lên column tương ứng.

---

## 3. Quy ước đặt tên FQN

Một số cột sử dụng **Fully Qualified Name (FQN)** với `_` làm dấu phân cách (script sẽ tự động chuyển `_` → `.`):

| Cột            | Ví dụ                            | Chuyển thành                    |
|----------------|----------------------------------|---------------------------------|
| Parent         | `Business_Revenue`               | `Business.Revenue`              |
| Related Terms  | `Business_Revenue;Business_Cost` | `Business.Revenue`, `Business.Cost` |
| Tags           | `PII_Sensitive;Tier_Tier1`       | `PII.Sensitive`, `Tier.Tier1`   |

---

## 4. Các lỗi validate thường gặp

Script sẽ kiểm tra (validate) **toàn bộ dữ liệu trước khi import**. Các lỗi thường gặp:

| Lỗi | Nguyên nhân | Cách sửa |
|------|-------------|----------|
| `File missing columns: [...]` | File Excel thiếu một hoặc nhiều cột bắt buộc | Thêm đúng tên cột còn thiếu vào dòng tiêu đề |
| `Column 'X' has empty rows` | Cột bắt buộc (Glossary/Term Name/Display Name/Description) bị trống | Điền giá trị vào ô trống |
| `Glossary does not exist: X` | Glossary chưa được tạo trong OpenMetadata | Tạo glossary trước qua giao diện hoặc API |
| `Parent term does not exist: X` | Không tìm thấy FQN của term cha | Đảm bảo term cha đã tồn tại; kiểm tra dấu `_` |
| `Table does not exist: X` | Không tìm thấy FQN của table trong OpenMetadata | Kiểm tra lại đường dẫn Service.Database.Schema.Table |
| `Column 'X' does not exist in Y` | Column không tồn tại trong table | Kiểm tra lại tên column |
| `Incomplete DB info: ...` | Chỉ điền một vài trong 5 cột DB | Điền đủ cả 5 cột hoặc bỏ trống cả 5 |
| `Owner does not exist: X` | Không tìm thấy username | Tạo user trước hoặc kiểm tra chính tả |
| `Reviewer does not exist: X` | Không tìm thấy username | Tạo user trước hoặc kiểm tra chính tả |
| `Related term does not exist: X` | Không tìm thấy FQN glossary term | Tạo term liên quan trước |
| `Invalid URL: X` | URL không đúng định dạng | Sửa lại URL (phải bắt đầu bằng `http://` hoặc `https://`) |
| `Tag does not exist: X` | Không tìm thấy FQN tag | Tạo tag/classification trước |

---

## 5. Lưu ý quan trọng

1. **Tạo glossary trước** — Glossary (VD: "Business") phải đã tồn tại trong OpenMetadata trước khi import term vào.
2. **Thứ tự dòng quan trọng khi có parent** — Nếu term B là con của term A, hãy đảm bảo term A nằm ở dòng trước (hoặc đã tồn tại trong OpenMetadata).
3. **Dùng dấu chấm phẩy (`;`) làm dấu phân cách** cho các trường nhiều giá trị: Synonyms, Related Terms, References, Tags.
4. **Dùng dấu gạch dưới (`_`) làm dấu phân cách FQN** trong các cột Parent, Related Terms, Tags.
5. **Để ô trống** (không ghi "N/A" hay "null") cho các trường tùy chọn không cần dùng.
6. **Nhiều file** — Có thể chia dữ liệu thành nhiều file Excel và cấu hình danh sách S3 key cách nhau bởi dấu phẩy.
