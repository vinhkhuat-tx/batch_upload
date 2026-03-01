# Hướng dẫn: Tạo file Excel để Import Dictionary

Hướng dẫn này mô tả định dạng file Excel (`.xlsx`) dùng cho script `upload_dictionary.py` để import **Data Dictionary** (mô tả table/column, tags, glossary terms) vào OpenMetadata.

---

## 1. Định dạng file

- **Loại file:** `.xlsx` (Excel)
- **Sheet:** Dữ liệu phải nằm ở **sheet đầu tiên**
- **Dòng tiêu đề:** Dòng 1 phải chứa đúng tên cột như bảng bên dưới
- Có thể chia dữ liệu thành **nhiều file** (cấu hình bằng danh sách key S3 cách nhau bởi dấu phẩy)

---

## 2. Danh sách cột

File Excel **bắt buộc** phải có đủ 9 cột bên dưới. Cột đánh dấu ★ không được để trống.

| # | Tên cột                  | Bắt buộc | Mô tả                                                                    |
|---|--------------------------|----------|---------------------------------------------------------------------------|
| 1 | **Service Name** ★       | Có       | Tên database service trong OpenMetadata (VD: `redshift-svc`)              |
| 2 | **Database Name** ★      | Có       | Tên database (VD: `analytics_db`)                                         |
| 3 | **Schema Name** ★        | Có       | Tên schema (VD: `public`)                                                |
| 4 | **Table Name** ★         | Có       | Tên table (VD: `fact_orders`)                                            |
| 5 | **Column Name** ★        | Có       | Tên column (VD: `order_id`)                                              |
| 6 | **Table Description**    | Không    | Mô tả cho table (hỗ trợ Markdown). Chỉ cần điền 1 lần cho mỗi table.    |
| 7 | **Column Description**   | Không    | Mô tả cho column cụ thể (hỗ trợ Markdown)                               |
| 8 | **Tags**                 | Không    | FQN các classification tag, cách nhau bởi `;`. Dùng `_` làm dấu phân cách |
| 9 | **Glossary Term**        | Không    | FQN các glossary term, cách nhau bởi `;`. Dùng `_` làm dấu phân cách    |

> **Lưu ý:** Mỗi dòng đại diện cho **một column** của table. Để mô tả nhiều column cùng table, dùng nhiều dòng với cùng giá trị Service/Database/Schema/Table.

---

## 3. Quy ước đặt tên FQN

Tags và Glossary Terms sử dụng **Fully Qualified Name (FQN)** với `_` làm dấu phân cách (script sẽ tự động chuyển `_` → `.`):

| Cột           | Ví dụ                                 | Chuyển thành                         |
|---------------|---------------------------------------|--------------------------------------|
| Tags          | `PII_Sensitive;Tier_Tier1`            | `PII.Sensitive`, `Tier.Tier1`        |
| Glossary Term | `Business_Revenue;Business_Cost`      | `Business.Revenue`, `Business.Cost`  |

---

## 4. Cách hoạt động của Import

Script xử lý từng dòng và thực hiện tối đa 3 thao tác:

```
Mỗi dòng:
  1. Tags + Glossary Term  →  PATCH /v1/tables/{id}  (gán lên column)
  2. Column Description    →  PUT  /v1/columns/name/{fqn}?entityType=table
  3. Table Description     →  PATCH /v1/tables/{id}  (cập nhật /description)
```

- **Tags & Glossary Terms** sẽ **thay thế** toàn bộ tags/terms hiện có trên column (xóa cũ trước, gán mới sau).
- **Table Description** được cập nhật qua path `/description` của table — giá trị giống nhau cho tất cả dòng cùng table, chỉ cần điền 1 lần.
- **Column Description** được cập nhật qua endpoint riêng cho column.

---

## 5. Các lỗi validate thường gặp

Script sẽ kiểm tra (validate) **toàn bộ dữ liệu trước khi import**. Các lỗi thường gặp:

| Lỗi | Nguyên nhân | Cách sửa |
|------|-------------|----------|
| `File missing columns: [...]` | File Excel thiếu một hoặc nhiều trong 9 cột bắt buộc | Thêm đúng tên cột còn thiếu vào dòng tiêu đề |
| `Column 'X' has empty rows` | Một trong 5 cột Service/Database/Schema/Table/Column Name bị trống | Điền giá trị — 5 cột này luôn bắt buộc |
| `Table does not exist: X` | Không tìm thấy FQN table (Service.Database.Schema.Table) trong OpenMetadata | Kiểm tra lại đường dẫn 4 phần |
| `Column 'X' does not exist in Y` | Column không tồn tại trong table chỉ định | Kiểm tra lại tên column |
| `Tag does not exist: X` | Không tìm thấy FQN tag trong OpenMetadata | Tạo classification/tag trước qua giao diện hoặc API |
| `Glossary Term does not exist: X` | Không tìm thấy FQN glossary term | Tạo glossary term trước (hoặc chạy `upload_glossary.py` trước) |

---

## 6. Lưu ý quan trọng

1. **Mỗi dòng = một column** — Mỗi dòng cập nhật metadata cho một column. Lặp lại thông tin table cho mỗi column.
2. **Table Description** chỉ cần điền ở một dòng của table (áp dụng cho cả table).
3. **Để ô trống** cho các trường tùy chọn — không ghi "N/A", "null" hay "none".
4. **Dùng dấu chấm phẩy (`;`) làm dấu phân cách** cho các trường Tags và Glossary Term nhiều giá trị.
5. **Dùng dấu gạch dưới (`_`) làm dấu phân cách FQN** trong các cột Tags và Glossary Term.
6. **Thứ tự import** — Nếu dictionary tham chiếu đến glossary terms, hãy chạy `upload_glossary.py` trước để đảm bảo các term đã tồn tại.
7. **Tags hiện có sẽ bị thay thế** — Khi điền Tags hoặc Glossary Term, script sẽ xóa giá trị cũ trên column đó và gán giá trị mới. Nếu để trống Tags/Glossary Term, giá trị hiện có được giữ nguyên.
8. **Nhiều file** — Có thể chia dữ liệu thành nhiều file Excel và cấu hình danh sách S3 key cách nhau bởi dấu phẩy.

---

## 8. Bắt đầu nhanh

```bash
# 1. Chuẩn bị file Excel theo định dạng ở trên
# 2. Upload lên S3
aws s3 cp my_dictionary.xlsx s3://pvc-temp/openmetadata/my_dictionary.xlsx

# 3. Cấu hình biến môi trường (hoặc sửa trực tiếp trong script)
export S3_BUCKET=pvc-temp
export S3_FILES=openmetadata/my_dictionary.xlsx
export MAIN_URL=http://localhost:8585/api

# 4. Chạy script
python upload_dictionary.py
```
