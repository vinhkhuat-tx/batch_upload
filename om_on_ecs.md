# Tài liệu kiến trúc & cấu hình OpenMetadata trên ECS

Tài liệu này mô tả kiến trúc triển khai OpenMetadata (OM) trên AWS ECS Fargate trong repo `services/ecs`, kèm các tham số cấu hình chính cho OpenMetadata Server, Airflow ingestion, OpenSearch và RDS PostgreSQL.

## Tổng quan kiến trúc (logic)
- **ALB (internal, HTTPS 443)** → target group HTTP 8585 → **ECS service `openmetadata-server`** (API/UI).
- **ECS service `openmetadata-ingestion`** chạy Airflow (UI/API 8080) cho ETL/metadata ingestion.
- Cả hai service đọc/ghi **RDS PostgreSQL** (`om_db`, `airflow_db`) và truy vấn **OpenSearch** cho chức năng search.
- **AWS Cloud Map (Private DNS)** `om-cloud-dwh.local` cho service discovery: `openmetadata-server.om-cloud-dwh.local`, `openmetadata-ingestion.om-cloud-dwh.local`.
- **Secrets Manager** cung cấp thông tin đăng nhập RDS, OpenSearch, Airflow; IAM task role chỉ cho phép đọc các secret cần thiết.
- **CloudWatch Logs** thu thập log của init, server và ingestion.

## Sơ đồ kiến trúc & luồng chính

```mermaid
flowchart LR
  subgraph Client
    U[User / BI tool]
  end

  subgraph Network[Private VPC]
    ALB[Internal ALB\nHTTPS 443]
    subgraph ECS[ECS Fargate]
      OM[openmetadata-server\nport 8585]
      ING[openmetadata-ingestion\n(Airflow) port 8080]
      INIT[openmetadata-init\n(one-off task)]
    end
    CM[(Cloud Map\nom-cloud-dwh.local)]
    SG[(Security Groups)]
  end

  subgraph Data[Data Services]
    RDS[(RDS PostgreSQL\nom_db / airflow_db)]
    OS[(OpenSearch\nHTTPS 443)]
  end

  subgraph Sec[Secrets & IAM]
    SM[(Secrets Manager\nRDS, OpenSearch, Airflow)]
    IAM[(IAM Task/Exec Roles)]
  end

  U -->|HTTPS / UI| ALB
  ALB -->|HTTP 8585| OM

  OM <-->|Service discovery\nopenmetadata-server.om-cloud-dwh.local| CM
  ING <-->|Service discovery\nopenmetadata-ingestion.om-cloud-dwh.local| CM

  OM -->|Metadata write/read| RDS
  OM -->|Search index/query| OS
  ING -->|Airflow metadata| RDS
  ING -->|Ingestion output\nPOST /api| OM
  ING -->|Task logs/metrics| RDS
  ING -->|Indexing hooks| OS

  INIT -->|Bootstrap/migrate| RDS
  INIT -->|Seed indices| OS

  SM -->|GetSecretValue| OM
  SM -->|GetSecretValue| ING
  SM -->|GetSecretValue| INIT
  IAM -.-> OM
  IAM -.-> ING
  IAM -.-> INIT

  CM -.-> OM
  CM -.-> ING

  ALB -. health .-> OM
  ING -. health .->|/health 8080| CM
  OM -. health .->|/signin 8585| ALB
```

**Luồng chính:**
- **UI/API**: Client ↔ (HTTPS) ALB → OM (8585).
- **Ingestion**: Airflow DAG tại `openmetadata-ingestion` gọi OM API (private DNS) để đẩy metadata; đọc/ghi RDS (`airflow_db`), gửi logs lên CloudWatch.
- **Search/Reindex**: OM ghi và reindex sang OpenSearch; Airflow có thể gọi hook để cập nhật index sau job.
- **Init/Migration**: Task `openmetadata-init` chạy `openmetadata-ops.sh migrate` để khởi tạo schema/seed và kiểm tra kết nối OpenSearch.
- **Secrets/IAM**: Task roles chỉ có quyền `secretsmanager:GetSecretValue`; không hardcode mật khẩu trong env.

## Các thành phần chính
### 1) OpenMetadata Server (`services/ecs/openmetadata_server.tf`)
- Fargate task: `cpu=1024`, `memory=2048`, image `var.om_image` (ví dụ `openmetadata-server-1.10.10`).
- Port 8585, health check `wget http://localhost:8585`.
- **ALB tùy chọn** (`enable_openmetadata_server_alb`):
  - Internal, listener HTTPS 443, target group HTTP 8585 với stickiness và health check `/signin`.
  - Domain: `alb_custom_domain`, certificate `alb_certificate` hoặc `alb_certificate_arn`, record A trong hosted zone `aft_hostedzone_name`.
- **Env chính** (đọc từ secrets):
  - DB: `DB_HOST/PORT/USER`, `DB_USER_PASSWORD`, `OM_DATABASE=om_db`, `DB_DRIVER_CLASS=org.postgresql.Driver`.
  - Search: `ELASTICSEARCH_HOST/PORT/SCHEME`, `ELASTICSEARCH_USER/PASSWORD`, `SEARCH_TYPE=opensearch`.
  - Airflow client: `AIRFLOW_HOST/PORT/USERNAME`, secret `PIPELINE_SERVICE_CLIENT_ENDPOINT`.
  - Auth: `AUTHENTICATION_PROVIDER=basic`, `AUTHENTICATION_AUTHORITY=${alb_custom_domain}`, `SERVER_HOST_API_URL=https://${alb_custom_domain}/api`.
- **IAM**: task & task exec role tự tạo, được phép `secretsmanager:GetSecretValue`, (tùy chọn) `sts:AssumeRole` theo `glue_assume_role_arns`.
- **Mạng**: SG ingress 8585 từ CIDR VPC; nếu gắn ALB thì mở từ SG của ALB. Egress all. Service registry gắn vào Cloud Map.

### 2) OpenMetadata Ingestion (Airflow) (`services/ecs/openmetadata_ingestion.tf`)
- Fargate task: `cpu=8192`, `memory=16384`, image `openmetadata-ingestion-1.10.4-custom-rest-py`.
- Chạy `airflow db migrate`, khởi tạo user admin, bật scheduler + webserver (8080).
- DB kết nối: `airflow_db` trong cùng RDS, string được dựng từ secret RDS (`rds_secret_name`).
- Secret Airflow (`airflow_secret_name`) được tạo kèm username/password, endpoint/web_url/api_url theo Cloud Map (`openmetadata-ingestion.om-cloud-dwh.local:8080`).
- SG ingress 8080 từ CIDR VPC; egress all. Đăng ký Cloud Map service `openmetadata-ingestion`.
- Logs: `/ecs/openmetadata-ingestion`, retention 7 ngày.
- IAM: task & exec role tự tạo, được phép đọc secret RDS + Airflow; tùy chọn assume Glue roles.

### 3) OpenMetadata Init (`services/ecs/openmetadata_init.tf`)
- Tác vụ Fargate one-off (không tạo service) để chạy `./bootstrap/openmetadata-ops.sh migrate`.
- Bật qua `enable_om_init`; chạy một lần qua `run_om_init` (tạo ECS service replica=1). Có thể ép chạy lại bằng `init_force_run`.
- Dùng cùng image OM, đọc secrets RDS/OpenSearch, log group `/ecs/openmetadata-init` (retention 30 ngày).

### 4) Kho dữ liệu & Search
- **RDS PostgreSQL**: Secret `rds_secret_name` (ví dụ `pvcb-dwh-dev/rds-cred/postgres-standalone`) chứa `host`, `port`, `username`, `password`.
  - OM dùng DB `om_db`; Airflow dùng `airflow_db` (được tạo khi migrate). Port mặc định 5432.
- **OpenSearch**: Secret `opensearch_secret_name` chứa `endpoint`, `username`, `password`. OM cấu hình `SEARCH_TYPE=opensearch`, TLS 443.

### 5) Mạng & DNS
- Triển khai trong VPC có sẵn `vpc_id` và các private subnets `subnets` (ví dụ 3 AZ). Fargate ENI không gán public IP (`assign_public_ip=true` chỉ khi cần).
- Cloud Map namespace cố định `om-cloud-dwh.local` để OM server gọi Airflow qua private DNS.
- ALB security group bổ sung truyền qua `alb_additional_security_group_ids`; ALB là internal (không IGW) trừ khi cấu hình khác.

### 6) Bảo mật & IAM
- Secrets Manager là nguồn duy nhất cho thông tin nhạy cảm; container env chỉ chứa giá trị không nhạy cảm.
- Task role giới hạn `secretsmanager:GetSecretValue`; exec role có quyền logs.
- Có thể cấp quyền Glue qua `glue_assume_role_arns` để OM khai thác catalog Glue.

### 7) Quan sát & Healthcheck
- CloudWatch Log Groups: `/ecs/openmetadata-init`, `/ecs/openmetadata-ingestion`, `/ecs/openmetadata-server`.
- Health checks: ALB `/signin` (server), ECS health `wget` 8585 (server), `curl /health` 8080 (Airflow).

## Tham số cấu hình chính (biến Terraform)
| Biến | Ý nghĩa | Ví dụ (dev `services/ecs/envs/dev/terraform.tfvars`) |
| --- | --- | --- |
| `region`, `vpc_id`, `subnets` | Vùng và subnet cho Fargate/ALB | `ap-southeast-1`, `[subnet-0295..., subnet-09ec..., subnet-0aea...]` |
| `cluster_name` | Tên ECS cluster | `ecs` |
| `om_image` | Image OpenMetadata server/init | `...:openmetadata-server-1.10.10` |
| `enable_openmetadata_server_alb` | Bật ALB cho OM server | `true` |
| `alb_custom_domain` | Domain cho ALB (private zone) | `om.apps.dap.dev.pvcombank.io` |
| `alb_certificate` / `alb_certificate_arn` | ACM cert wildcard | `*.apps.dap.dev.pvcombank.io` |
| `aft_hostedzone_name` | Hosted zone để tạo record | `dap.dev.pvcombank.io` |
| `alb_additional_security_group_ids` | SG gắn vào ALB | `["sg-0940e549efb52cce1"]` |
| `rds_secret_name` | Secret RDS PostgreSQL | `pvcb-dwh-dev/rds-cred/postgres-standalone` |
| `opensearch_secret_name` | Secret OpenSearch | `pvcb-dwh-dev-opensearch-credentials` |
| `airflow_secret_name` | Secret Airflow (tự tạo) | `airflow-credentials` |
| `glue_assume_role_arns` | Danh sách role Glue được phép assume | `["arn:aws:iam::474668419121:role/datalake-dev-glue-openmetadata-role"]` |
| `enable_om_init`, `run_om_init`, `init_force_run` | Điều khiển task init | `true/false` |

## Quy trình triển khai (tóm tắt)
1. **Chuẩn bị hạ tầng có sẵn**: VPC & private subnets, RDS PostgreSQL, OpenSearch, ACM certificate khớp domain, hosted zone Route53, các Secret RDS/OpenSearch đã tạo.
2. Cập nhật `terraform.tfvars` trong `services/ecs/envs/<env>/` với domain, cert, secret, subnet, image.
3. (Tuỳ chọn) Bật `enable_om_init` và `run_om_init` lần đầu để migrate DB; tắt lại sau khi hoàn tất.
4. Apply Terraform ở thư mục `services/ecs` với workspace/env tương ứng (đảm bảo remote state bucket/role đúng).
5. Kiểm tra: record Route53 → ALB, health check ALB ok, Cloud Map có `openmetadata-server` & `openmetadata-ingestion`, UI OM truy cập qua `https://<alb_custom_domain>`.

## Vận hành & bảo trì
- **Scale**: chỉnh `desired_count`, CPU/memory trong module service; autoscaling server đang tắt (`enable_autoscaling=false`).
- **Rotate secret**: cập nhật Secrets Manager → ECS task dùng chế độ Fargate, cần "force new deployment" để nhận giá trị mới.
- **Nâng cấp image**: đổi `om_image` hoặc image ingestion rồi `terraform apply`.
- **Giám sát**: CloudWatch Logs + ALB target health; cân nhắc metric alarms cho 5xx/latency.
- **Backup**: bật snapshot RDS, snapshot OpenSearch theo chính sách riêng (ngoài scope Terraform này).

## Liên hệ
- Chủ sở hữu: Nhóm DWH/Platform.
- Kênh hỗ trợ: gửi ticket nội bộ hoặc ping đội hạ tầng khi cần thay đổi VPC/ALB/cert.
