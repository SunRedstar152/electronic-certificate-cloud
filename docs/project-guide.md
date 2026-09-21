# คู่มือระบบและโครงสร้างโปรเจกต์ทั้งหมด

**PKRU Electronic Certificate** | ฉบับอธิบายการทำงานและหน้าที่ไฟล์

อ้างอิงซอร์สใน `D:\ENDPRO\Electronic Certificate-demosd` เวอร์ชัน `3c6642f` วันที่ 9 กันยายน 2569

## 1. ภาพรวมของระบบ

โปรเจกต์นี้เป็นเว็บออกและตรวจสอบเกียรติบัตรภาษาไทยสำหรับหลายหน่วยงาน ผู้ดูแลสร้างหน่วยงานและบัญชีผู้ใช้ หน่วยงานจัดแม่แบบและออกเกียรติบัตร ส่วนผู้รับใช้รหัสหรือ QR เปิดตรวจสอบและดาวน์โหลดใบได้

ตัวเว็บประกอบด้วย **หน้า HTML/CSS/JavaScript + API Python/FastAPI + PostgreSQL + พื้นที่เก็บไฟล์** การนำไปใช้งานปัจจุบันจัดบริการด้วย Docker Compose

### ข้อมูลเดินทางผ่านส่วนใดบ้าง

| ลำดับ | ส่วนที่ทำงาน | ผลที่ส่งต่อ |
| --- | --- | --- |
| 1 | เบราว์เซอร์โหลดหน้าใน `static/` | แสดงแบบฟอร์มและรับคำสั่งผู้ใช้ |
| 2 | JavaScript เรียก API ผ่าน `api-client.js` | ส่งข้อมูลและ Bearer token |
| 3 | `main.py` ส่งคำขอไป `routers/` | เลือกฟังก์ชันตาม URL และ HTTP method |
| 4 | `auth.py` และ `schemas/` | ตรวจบัญชี สิทธิ์ และรูปแบบข้อมูล |
| 5 | `services/` | ใช้กฎงาน เช่น ออกใบ อ่าน Excel หรือสร้างภาพ |
| 6 | `database.py` / `models.py` และ storage | บันทึกแถวข้อมูลและไฟล์ที่เกี่ยวข้อง |
| 7 | Router ส่ง JSON หรือไฟล์กลับ | หน้าเว็บแสดงผลหรือให้ดาวน์โหลด |

### บทบาทผู้ใช้จริง

- **ผู้ดูแลระบบ (`admin`)**: ดูภาพรวม จัดการหน่วยงานและบุคลากร ตั้งรหัสผ่านใหม่ เปิด/ปิดบัญชี และดูบันทึกการใช้งาน
- **ผู้ใช้หน่วยงาน (`organization`)**: จัดแม่แบบและฟอนต์ ออกใบรายบุคคล/กลุ่ม ติดตามผล ดาวน์โหลด และเพิกถอนใบของหน่วยงานตนเอง
- **ผู้ตรวจสอบสาธารณะ**: ใช้รหัสเกียรติบัตรหรือ QR โดยไม่ต้องเข้าสู่ระบบ ข้อมูลที่เปิดเผยถูกกำหนดใน `routers/public.py`

### วิธีอ่านเอกสารนี้

บท 2 อธิบายเทคโนโลยี บท 3-5 อธิบายหน้าจอและลำดับงาน บท 6-8 อธิบายข้อมูล ความปลอดภัย Docker และการดูแล ภาคผนวกแจกแจงตารางและคอลัมน์ ค่าตั้ง API และไฟล์ซอร์สทุกไฟล์ที่ Git ติดตามในรุ่นนี้ ส่วนไฟล์ที่เกิดระหว่างใช้งานอธิบายตามประเภทและตำแหน่งจัดเก็บ

เอกสารฉบับนี้อยู่ที่ `docs/project-guide.md` และ `docs/project-guide.pdf` ในโปรเจกต์ ใช้ควบคู่กับ `README.md` สำหรับคำสั่งเริ่มต้น และ `docs/deployment.md` สำหรับงานนำระบบขึ้นใช้งาน

<!-- pagebreak -->

## 2. เทคโนโลยีและไลบรารีที่ใช้

เวอร์ชันต่อไปนี้มาจาก `requirements.txt` ของโปรเจกต์ เป็นเวอร์ชันที่ตรึงไว้สำหรับติดตั้ง มิใช่รายการเวอร์ชันล่าสุดจากอินเทอร์เน็ต

| เทคโนโลยี / เวอร์ชัน | ใช้ทำอะไร |
| --- | --- |
| Python 3.13 / Debian Bookworm | สภาพแวดล้อมเว็บใน Docker image |
| HTML, CSS, JavaScript | หน้าเว็บและการโต้ตอบ ใช้ JavaScript โดยตรง |
| FastAPI 0.141.1 | ประกาศ API, dependencies, validation และเอกสาร OpenAPI |
| Uvicorn 0.52.1 | ASGI server รับ HTTP ที่พอร์ต 8000 |
| SQLAlchemy 2.0.51 | ORM, transaction, query และการเชื่อมฐานข้อมูล |
| psycopg 3.3.4 / psycopg2-binary 2.9.12 | PostgreSQL drivers; Docker สร้าง URL แบบ `postgresql+psycopg` |
| PostgreSQL 18 | ฐานข้อมูลหลัก พร้อม JSONB, constraints และ row/advisory locks |
| Alembic 1.19.1 | อัปเกรดโครงสร้างฐานข้อมูลเดิมตาม migration |
| Pydantic 2.13.4 | โมเดลรับเข้า/ส่งออกและตรวจชนิดข้อมูล |
| pydantic-settings 2.15.0 | โหลดค่าตั้งจาก environment และ `.env` |
| python-jose 3.5.0 | สร้างและตรวจ JWT ด้วยกุญแจระบบ |
| pwdlib[argon2] 0.3.1 | hash และตรวจรหัสผ่านผู้ใช้ |
| python-multipart 0.0.32 | รับแบบฟอร์มและไฟล์อัปโหลด |
| Pillow 12.3.0 | เปิดภาพ วาดเกียรติบัตร และแปลง JPG/PDF |
| qrcode 8.2 | สร้าง QR จาก URL ตรวจสอบเกียรติบัตร |
| libraqm / HarfBuzz / FriBiDi | จัดรูปอักษรและวรรณยุกต์ในการสร้างภาพ |
| pythainlp 5.3.5 / regex 2026.7.19 | ตัดคำไทยและจัดกลุ่มอักขระสำหรับวางข้อความ |
| openpyxl 3.1.5 | อ่านข้อมูลและรูปแบบเซลล์ใน Excel `.xlsx` |
| fonttools 4.63.0 | ตรวจรูปแบบและอักขระที่ฟอนต์รองรับ |
| APScheduler 3.11.3 | งานล้าง audit log ตามอายุที่ตั้งไว้ |
| click 8.4.2 | คำสั่ง CLI เช่น สร้างผู้ดูแลครั้งแรก |

`requirements-dev.txt` รวม runtime ข้างต้นและเพิ่ม `httpx 0.28.1` กับ `pytest 9.1.1` สำหรับตรวจโค้ด ชุด JavaScript ใช้ Node.js test runner ส่วนไฟล์ `.browser.cjs` ใช้ Playwright ตามสภาพแวดล้อมที่เตรียมให้ การเปิดเว็บใน Docker ไม่ต้องรัน Node.js หรือขั้นตอน build frontend

ฟอนต์ที่แนบมาคือ Sarabun, Noto Sans Thai และ TH Niramit AS อย่างละ Regular/Bold พร้อมไฟล์ใบอนุญาตใน `static/fonts/`

<!-- pagebreak -->

## 3. หน้าจอและระบบการจัดการ

### หน้าเว็บหลัก

| หน้า / ไฟล์ | มีอะไรในหน้า |
| --- | --- |
| `/` → `static/index.html` | ค้นหาด้วยรหัส เปิดรายละเอียด ตรวจสถานะ ดูภาพ และดาวน์โหลดใบ |
| `/login` → `static/login.html` | ชื่อผู้ใช้ รหัสผ่าน แสดง/ซ่อนรหัส และข้อความแจ้งข้อผิดพลาด |
| `/dashboard` → `static/dashboard.html` | เมนูเปลี่ยนตามบทบาท พร้อมหน้าต่างแม่แบบ พรีวิว และลายเซ็น |

### เมนูผู้ดูแลระบบ

1. **ภาพรวม**: แสดงสถิติที่ดึงจาก `/admin/stats`
2. **หน่วยงาน**: สร้างและแก้ชื่อ/ข้อมูลติดต่อ เปิดหรือปิดหน่วยงาน ดูจำนวนบุคลากร
3. **จัดการบุคลากรของหน่วยงาน**: เพิ่มชื่อผู้ใช้ เปิด/ปิดบัญชี และตั้งรหัสผ่านใหม่
4. **บันทึกการใช้งาน**: ดูรายการกิจกรรมที่บันทึกไว้ พร้อมข้อมูลเป้าหมายและตัวกรองที่ API รองรับ

การสร้างหน่วยงานใช้ `services/accounts.py` และ `organization_identity.py` ตรวจชื่อซ้ำ สร้างรหัสหน่วยงาน สร้างบัญชีแรก และเตรียมแม่แบบต้นฉบับ ข้อมูลหน่วยงานกับบัญชีอยู่คนละตารางแต่เชื่อมกันด้วย `organization_id`

### เมนูหน่วยงาน

| เมนู | งานที่ทำได้ |
| --- | --- |
| ภาพรวม | สรุปข้อมูลของหน่วยงานจากรายการที่โหลดมา |
| แม่แบบ | เพิ่มพื้นหลัง ทำสำเนา แก้ตำแหน่ง จัดกึ่งกลาง ปิดใช้ และลบ |
| ออกเกียรติบัตรรายบุคคล | เลือกแม่แบบ กรอกข้อมูล ใส่ลายเซ็น ดูตัวอย่าง และยืนยัน |
| ออกเกียรติบัตรแบบกลุ่ม | อ่าน Excel จับคู่คอลัมน์ ตรวจข้อมูล และออกหลายใบ |
| รายการประมวลผล | ดูจำนวนสำเร็จ/ล้มเหลว ลองรายการที่ล้มเหลวอีกครั้ง และโหลด ZIP |
| เกียรติบัตร | ค้นหา ดูรายการ ดาวน์โหลด และเพิกถอนพร้อมเหตุผล |

การอัปโหลด/เลือก/ปิดใช้ฟอนต์อยู่ในส่วนแก้ไขแม่แบบ ไม่ใช่เมนูหลักแยกอีกหน้า `dashboard.js` ควบคุมหน้าจอหลัก ส่วน `organization-admin.js`, `template-layout.js` และ `signature-pad.js` แยกตรรกะเฉพาะด้าน

### ส่วนที่มี API แต่ยังไม่มีเมนูหลัก

โค้ดมี `/activities` สำหรับกิจกรรมและการอนุมัติ และ `/certificate-types` สำหรับประเภทเกียรติบัตร พร้อมโมเดลและการตรวจสิทธิ์แล้ว แต่ Dashboard ปัจจุบันยังไม่มีเมนูสำหรับสองส่วนนี้โดยตรง ต้องแยกความพร้อมของ API ออกจากความพร้อมของหน้าจอ

<!-- pagebreak -->

## 4. ลำดับการทำงาน: เข้าระบบและออกใบ

### 4.1 เข้าสู่ระบบ

1. `static/js/auth.js` รับข้อมูล แล้ว `api-client.js` ส่งแบบฟอร์มไป `POST /login`
2. `routers/auth.py` เรียก `services/login_throttle.py` ตรวจโควตาจาก IP และคู่ชื่อผู้ใช้/IP
3. อ่านผู้ใช้กับหน่วยงาน ตรวจ password hash และสถานะเปิดใช้งาน
4. เมื่อตรงกัน `auth.py` สร้าง JWT HS256 อายุเริ่มต้น 24 ชั่วโมง มี user ID, role และ organization ID
5. เบราว์เซอร์เก็บ token ใน localStorage ชื่อ `pkru_access_token` และส่ง `Authorization: Bearer ...` เมื่อเรียก API ที่ต้องล็อกอิน
6. API อ่านผู้ใช้จากฐานข้อมูลอีกครั้ง การปิดบัญชีหรือปิดหน่วยงานจึงทำให้ token เดิมใช้ระบบต่อไม่ได้ การออกจากระบบบนหน้าเว็บลบ token ที่เก็บในเบราว์เซอร์

### 4.2 ออกเกียรติบัตรรายบุคคล

1. หน้าเว็บเลือกแม่แบบที่ใช้งาน กรอกชื่อ หัวข้อ วันออกและช่องที่แม่แบบกำหนด พร้อมลายเซ็นแต่ละบล็อก
2. ส่งข้อมูลไป `POST /certificates` พร้อม `Idempotency-Key` สำหรับการยืนยันครั้งนั้น
3. Router ตรวจ Pydantic schema และบทบาท ก่อนเรียก `services/certificates.py`
4. Service ตรวจเจ้าของแม่แบบ ช่องบังคับ วันที่/เวลา และกิจกรรมที่อ้างอิงต้องอนุมัติแล้ว หากส่ง key เดิมจะค้นหาใบเดิมเพื่อป้องกันการสร้างซ้ำ
5. จองแถวเกียรติบัตรและ `verification_code` ที่ไม่ซ้ำ จากนั้น `image_builder.py` เปิดพื้นหลังและวาดช่องตาม layout
6. `value_formatter.py` จัดค่าที่แสดง `text_layout.py` วางข้อความไทย `signatures.py` เตรียมลายเซ็น และ `qrcode` สร้าง QR
7. เขียน PNG ชั่วคราวให้เสร็จ จึงเผยแพร่ไฟล์ชื่อเฉพาะและบันทึก `image_path` พร้อม audit log ในฐานข้อมูล
8. API คืนข้อมูลใบที่สร้าง หน้าเว็บแสดงผลและปุ่มดาวน์โหลด หากเกิดข้อผิดพลาด service rollback และตรวจผล commit ก่อนจัดการไฟล์ เพื่อไม่ลบภาพที่บันทึกสำเร็จไปแล้ว

### 4.3 ตรวจสอบและเพิกถอน

ผู้รับเปิด QR ที่ชี้ไป `PUBLIC_BASE_URL/?code=รหัส` แล้วหน้าเว็บเรียก `/api/public/certificates/{code}` ข้อมูลสาธารณะประกอบด้วยชื่อผู้รับ หัวข้อ วันออก หน่วยงาน รหัสและสถานะ รวมข้อมูลกิจกรรม/ประเภทเมื่อมี

PNG ใช้ภาพที่ออกไว้ JPG และ PDF แปลงจากภาพนั้นในหน่วยความจำ **PDF เกียรติบัตรจึงเป็นภาพใน PDF** เมื่อเพิกถอนจะเก็บเหตุผล เวลา และผู้ดำเนินการ การค้นหายังเห็นสถานะ revoked แต่การขอภาพ/ดาวน์โหลดผ่านเส้นทางสาธารณะตอบ 410

<!-- pagebreak -->

## 5. แม่แบบ ลายเซ็น QR และการนำเข้ากลุ่ม

### โครงสร้างแม่แบบ

ภาพพื้นหลังเป็น PNG/JPEG ส่วนตำแหน่งเก็บเป็น JSONB ใน `templates.layout` รูปแบบ version 2 มีรายการ `elements` แต่ละชิ้นมี ID, แหล่งข้อมูล, ประเภท, ป้ายชื่อ, สถานะบังคับ/แสดง, กล่อง และรูปแบบข้อความ

พิกัด `x`, `y`, `width`, `height` ใช้สัดส่วน 0-1 ของภาพจริง เช่น x=0.5 คือกึ่งกลางแนวนอน จึงย้ายตำแหน่งตามขนาดภาพได้ ฟอนต์เก็บขนาดเป็นพิกเซลของภาพต้นฉบับ รูปแบบเดิม `positions` ยังมีตัวแปลงสำหรับข้อมูลเก่า

ชนิดช่องมี text, multiline, date, time, date_range, time_range, qr และ signature แหล่งข้อมูลมาตรฐานคือชื่อผู้รับ หัวข้อ วันออก URL/รหัสตรวจสอบ และลายเซ็น ส่วนช่องกำหนดเองใช้ `custom.<key>`

- `template-layout.js` คำนวณลาก/ย่อขยาย จัดกลางและ snapping; `dashboard.js` จัดเหตุการณ์และบันทึกไป API
- ข้อความทั่วไปคงขนาดตัวอักษรและเขียนเลยบล็อกได้ ส่วน multiline ตัดบรรทัดตามความกว้าง ภาพจริงตัดเมื่อเลยขอบหน้ากระดาษ
- QR และ ID เป็นคนละช่อง ID ใช้ source `verification_code` จึงเปลี่ยนตามใบ และปรับสี/ฟอนต์ได้ ข้อมูลเก่ายังรองรับ caption ใต้ QR
- ลายเซ็นส่งเป็น PNG โปร่งใสพร้อมชื่อและตำแหน่ง แยกด้วย element ID วาดลงภาพสุดท้ายแล้วปิดข้อมูลภาพชั่วคราว ไม่ได้มีตารางคลังลายเซ็นถาวร
- ต้นฉบับเก็บที่ `assets/templates/original/` ใช้ภาพ 2000 x 1414 และ `template.json` ระบบสร้างรายการให้หน่วยงานด้วย key `original`; ป้องกันแก้/ลบต้นฉบับ แต่ทำสำเนาได้
- ลบแม่แบบของหน่วยงานด้วย `deleted_at` เพื่อคงความสัมพันธ์กับใบเก่าที่ออกแล้ว

### ออกเกียรติบัตรแบบกลุ่ม

1. อัปโหลด `.xlsx` → `/batches/headers` อ่านหัวคอลัมน์ → ผู้ใช้จับคู่กับช่องของแม่แบบ
2. `/batches/preview` ตรวจค่าทีละแถว เช่น ชื่อ รหัส วันที่ และช่องบังคับ แล้วแสดงข้อผิดพลาดก่อนยืนยัน
3. `POST /batches` บันทึกงานและ `batch_rows` จากข้อมูลที่ยืนยัน ไม่ต้องเก็บไฟล์ Excel ต้นฉบับเป็นแหล่งข้อมูลหลัก
4. `/batches/{id}/process` ประมวลผลผ่าน service ทีละแถว มีการ claim แถวและใช้ idempotency key แบบ batch/row
5. สรุปเป็น completed, partial หรือ failed; `/retry` จัดการรายการล้มเหลว แล้ว `/download` สร้าง ZIP และ `manifest.csv` แสดงผลรายการในชุด

การประมวลผลกลุ่มทำในคำขอ API ผ่าน service ปัจจุบัน ไม่ได้ใช้ Celery/Redis หรือ worker คิวแยก CSV ที่เหลือใน ZIP เป็นรายการผลลัพธ์ ไม่ใช่ช่องทางนำเข้า CSV บนหน้าเว็บ

<!-- pagebreak -->

## 6. ฐานข้อมูล การจัดเก็บ และความปลอดภัย

ฐานข้อมูลหลักชื่อ `pkru_certificate` ใช้ PostgreSQL 18 และ schema `public` การเชื่อมต่อใน `database.py` กำหนด `search_path=public` มีตารางแอป 12 ตาราง และ `alembic_version` สำหรับเวอร์ชันโครงสร้าง

### ความสัมพันธ์หลัก

- `organizations` เป็นเจ้าของ users, templates, font_assets, batches, certificates และ activities
- `batches` มีหลาย `batch_rows`; แถวที่สำเร็จอ้างถึง `certificates`
- `certificates` อ้างแม่แบบ ผู้สร้าง งานกลุ่ม และกิจกรรม/ประเภทเมื่อระบุ
- `activities` เชื่อม `activity_approval_logs`; `audit_logs` บันทึกการกระทำของผู้ใช้
- `login_attempt_buckets` เก็บตัวนับป้องกันเดารหัส โดยไม่อ้างชื่อผู้ใช้หรือ IP แบบข้อความตรง

ใช้ primary/foreign/unique keys และ check constraints ร่วมกับเงื่อนไขหน่วยงานใน query เช่น ชื่อผู้ใช้และรหัสตรวจสอบต้องไม่ซ้ำ และบางความสัมพันธ์ใช้ foreign key แบบหลายคอลัมน์เพื่อผูกข้อมูลกับหน่วยงานเดียวกัน

### ข้อมูลชนิดใดอยู่ที่ไหน

| ข้อมูล | ตำแหน่ง |
| --- | --- |
| หน่วยงาน บัญชี แม่แบบและ layout รายการใบ | PostgreSQL ผ่าน `models.py` |
| password hash ผู้ใช้ | `public.users.password_hash` |
| ค่าลับระบบและรหัสเชื่อม DB | `.env.docker` บน host; `.env` สำหรับรัน Python ตรง |
| ภาพแม่แบบอัปโหลด | `/app/storage/uploads/templates` |
| ฟอนต์ที่หน่วยงานอัปโหลด | `/app/storage/uploads/fonts` |
| ภาพใบที่ออกแล้ว | `/app/storage/generated/certificates` |
| แม่แบบต้นฉบับและฟอนต์ระบบ | `assets/` และ `static/fonts/` ซึ่งติดมากับ image |
| ชุดสำรองฐานข้อมูลพร้อมไฟล์ | `backups/` บน host |

### กฎป้องกันที่โค้ดมีอยู่

รหัสผ่านใช้ Argon2 ผ่าน pwdlib; JWT ตรวจด้วย SECRET_KEY; API ตรวจบทบาทและเจ้าของข้อมูล ไม่อาศัยการซ่อนเมนูเพียงอย่างเดียว การเดารหัสจำกัด 5 ครั้งต่อชื่อผู้ใช้/IP และรวม 30 ครั้งต่อ IP ใน 15 นาที ใช้ row lock ใน PostgreSQL เพื่อให้หลายคำขอเห็นตัวนับเดียวกัน

ภาพ ฟอนต์ ลายเซ็นและ Excel ถูกตรวจชนิด/ขนาดก่อนใช้ เส้นทางไฟล์ต้องอยู่ในพื้นที่ที่จัดการไว้ StaticFiles ปิดการเข้าถึง uploads/generated/certificates โดยตรง การอ่านใบใช้ API ที่ตรวจสิทธิ์หรือรหัสสาธารณะ

`core/errors.py` จัด error envelope แบบเดียวกัน มี code, message, correlation_id และไม่ส่ง traceback ภายในกลับผู้ใช้ `services/audit.py` ปิดบังค่าที่ชื่อ password/token/secret ในรายละเอียด log

<!-- pagebreak -->

## 7. Docker การสำรองและงานตามเวลา

### บริการใน `compose.yaml`

| Service | เริ่มเมื่อใด / หน้าที่ |
| --- | --- |
| db | PostgreSQL `18-alpine`; เก็บข้อมูลใน volume `postgres_data` ที่ `/var/lib/postgresql` |
| migrate | รอ db healthy; สร้าง/อัปเกรดตารางครั้งเดียวแล้วจบ `Exited (0)` |
| web | รอ migration สำเร็จ; ใช้ image `pkru-certificate:local` รัน Uvicorn |
| backup | รอ DB และ migration; อ่าน storage อย่างเดียวและเขียนชุดสำรองบน host |
| scheduler | เปิดเมื่อเลือก profile `maintenance`; ล้าง audit log ตามอายุ |

Dockerfile เว็บใช้ผู้ใช้ app (UID 10001) ติดตั้งตัวจัดรูปอักษรไทย และคัดลอกเฉพาะไฟล์ runtime ที่ระบุไว้ `container_entrypoint.py` สร้าง DATABASE_URL จากตัวแปร PostgreSQL โดยเข้ารหัสส่วน URL ให้ถูกต้องก่อนเริ่มคำสั่ง

`initialize_database.py` ใช้ advisory lock กัน migration พร้อมกัน ถ้า public ยังว่างจะอ่าน **`sql/schema.sql`** ถ้ามีข้อมูลเดิมจะเรียก `alembic upgrade head` ตามประวัติ หากพบตารางเดิมแต่ไม่มี alembic_version จะหยุดให้ตรวจโครงสร้างก่อน

### ใช้งานทั่วไป

```powershell
cd 'D:\ENDPRO\Electronic Certificate-demosd'
.\scripts\docker.ps1 start
.\scripts\docker.ps1 status
.\scripts\docker.ps1 stop
```

เปิดเว็บที่ `http://localhost:8000` การตั้งค่าปัจจุบันใช้ loopback จึงยังไม่ใช่ URL สำหรับคนละเครื่อง การเปลี่ยนซอร์สต้อง build image ใหม่ การหยุด service เก็บ volumes ไว้ แต่ `down -v` ลบ volumes ด้วย

### ระบบสำรองข้อมูล

`docker/backup.Dockerfile` ใช้ PostgreSQL 18 client กับ Python standard library `backup.py` ล็อกตารางขณะเก็บ snapshot ฐานข้อมูลและไฟล์ให้สอดคล้องกัน เขียน dump/tar/manifest ตรวจ SHA-256 แล้วเปลี่ยนชื่อชุดให้สมบูรณ์ เก็บชุดอัตโนมัติล่าสุด 14 ชุด และสำรองทุกวัน 03:00 น. เวลาไทยเมื่อ Docker ทำงาน

`.\scripts\backup.ps1 run` สำรองทันที, `status` ดูผล และ `verify` กู้คืนจริงใน PostgreSQL 18 ชั่วคราวบน tmpfs เปรียบเทียบตารางกับไฟล์ แล้วลบคอนเทนเนอร์ ผลอยู่ใน `restore-verification.json` ชุดสำรองนี้ยังอยู่บนเครื่องเดียวกัน การสำรองไปที่อื่นต้องกำหนดปลายทางเพิ่มเติม

### Scheduler คนละงานกับ backup

`tasks.py` ใช้ APScheduler ล้าง audit log ที่เก่ากว่า `AUDIT_RETENTION_DAYS` (365 วันโดยปริยาย) ตาราง job store `apscheduler_jobs` จะเกิดเมื่อเริ่ม scheduler ไม่ใช่หนึ่งใน 12 ตารางโมเดลแอป ค่าเริ่มต้นเปิดเฉพาะ web/db/backup และไม่เปิด profile นี้ ควรมี scheduler ที่ทำงานเพียงตัวเดียว

<!-- pagebreak -->

## 8. การดูแลโค้ด ข้อจำกัด และจุดที่ต้องรู้

### เมื่อต้องการปรับระบบ ควรเริ่มที่ไฟล์ไหน

| งาน | ไฟล์ที่เกี่ยวข้อง |
| --- | --- |
| เปลี่ยนหน้าจอ/เมนู | `static/dashboard.html`, `static/js/dashboard.js`, `static/css/dashboard.css` |
| เปลี่ยนการจัดตำแหน่งแม่แบบ | `static/js/template-layout.js`, `schemas/template_layout.py`, `services/image_builder.py` |
| เปลี่ยนการวางภาษาไทย | `services/text_layout.py`, `services/value_formatter.py` |
| เปลี่ยนลายเซ็นหรือ ID | `static/js/signature-pad.js`, `services/signatures.py`, `services/image_builder.py` |
| เปลี่ยนแม่แบบต้นฉบับระบบ | `assets/templates/original/`, `services/builtin_templates.py` |
| เปลี่ยนข้อมูล/กฎการออกใบ | `schemas/certificates.py`, `services/certificates.py`, `routers/certificates.py` |
| เปลี่ยนการอ่าน Excel | `services/import_parser.py`, `services/batch_service.py` |
| เปลี่ยนบัญชี/สิทธิ์ | `auth.py`, `services/accounts.py`, `routers/admin.py`, `repositories/scoping.py` |
| เปลี่ยนโครงสร้างฐานข้อมูล | `models.py`, migration ใหม่ และสร้าง `sql/schema.sql` ใหม่ด้วย `scripts/export_schema.py` |
| เปลี่ยนรอบสำรอง | `.env.docker`, `compose.yaml`, `scripts/backup.py` |

### ข้อจำกัดที่กำหนดในโค้ด

- ภาพแม่แบบ PNG/JPEG สูงสุด 10 MiB; ฟอนต์ TTF/OTF สูงสุด 5 MiB และตรวจอักษรไทย
- ลายเซ็น PNG สูงสุด 2 MiB, 4096 x 2048 พิกเซล ต้องมีความโปร่งใสและมีเส้นที่มองเห็น
- Excel สูงสุด 10 MiB, 5,000 แถวข้อมูล และ 30 คอลัมน์ มีการจำกัด ZIP/XML ภายในด้วย
- Layout สูงสุด 100 elements, JSON 64 KiB; ขนาดฟอนต์ที่ตั้งได้ 8-512 พิกเซล
- กล่องเป็นสัดส่วนช่วยรองรับหลายขนาด แต่ QR/ตัวอักษรต้องมีพิกเซลเพียงพอจึงจะอ่านหรือสแกนได้

### พฤติกรรมที่ควรแยกให้ชัด

กิจกรรมมีสถานะ DRAFT → PENDING_APPROVAL → APPROVED หรือ REJECTED โค้ดอนุญาตทั้ง admin และบัญชีหน่วยงานตัดสินกิจกรรม โดยบัญชีหน่วยงานเข้าถึงได้เฉพาะของตน จึงยังไม่ได้บังคับแยกผู้เสนอออกจากผู้อนุมัติ ส่วน CANCELLED มีใน enum แต่ไม่มี route ยกเลิกในรุ่นนี้

กิจกรรมและประเภทเกียรติบัตรเป็นข้อมูลเสริมที่ API รองรับ การออกใบที่ส่ง activity_id จะตรวจว่าอนุมัติแล้ว หน้าจอปัจจุบันยังต้องพัฒนาต่อหากต้องการจัดการสองส่วนนี้จากเมนูโดยตรง

`tests/` และ `scripts/verify_release*` เป็นเครื่องมือพัฒนา ไม่ได้เริ่มตอนเปิด Docker; ชุดฐานข้อมูลใน tests ต้องกำหนด TEST_DATABASE_URL สำหรับพื้นที่ตรวจที่เหมาะสม ส่วน `seed_demo.py` สร้างข้อมูลตัวอย่างเมื่อเรียกเอง และไม่ได้ถูกรวมใน image เว็บ ไม่ใช่ขั้นตอนเริ่มระบบจริง

การนำขึ้นใช้งานจากภายนอกยังต้องตั้งโดเมน/IP, HTTPS, PUBLIC_BASE_URL และ trusted proxy ตามสภาพแวดล้อม การเปลี่ยนค่าเหล่านี้แยกจากการทำงานบน localhost ที่เปิดอยู่แล้ว

<!-- pagebreak -->

## ภาคผนวก ก - ตารางและคอลัมน์ (1/3)

รายการคอลัมน์มาจาก `models.py` รวม created_at/updated_at ที่รับจาก TimestampMixin ชนิดและ constraints ฉบับ SQL เต็มอยู่ใน `sql/schema.sql`

### `organizations` - หน่วยงานและช่องทางติดต่อ

`id` (integer / PK), `name` (varchar(255)), `code` (varchar(32)), `contact_name` (varchar(255) / nullable), `contact_email` (varchar(320) / nullable), `contact_phone` (varchar(50) / nullable), `status` (enum RecordStatus), `created_at` (timestamptz), `updated_at` (timestamptz)

### `users` - บัญชี บทบาท สถานะและ hash รหัสผ่าน

`id` (integer / PK), `username` (varchar(100)), `password_hash` (varchar(255)), `role` (enum UserRole), `organization_id` (integer / nullable), `is_active` (boolean), `created_at` (timestamptz), `updated_at` (timestamptz)

### `login_attempt_buckets` - ตัวนับล็อกอินที่หมดอายุได้

`bucket_key` (varchar(64) / PK), `failed_attempts` (integer), `expires_at` (timestamptz)

### `templates` - แม่แบบ รูปพื้นหลัง layout และสถานะลบ

`id` (integer / PK), `organization_id` (integer), `name` (varchar(255)), `background_image_path` (varchar(1024)), `builtin_key` (varchar(64) / nullable), `deleted_at` (timestamptz / nullable), `positions` (JSONB), `dimensions` (JSONB), `layout` (JSONB), `status` (enum RecordStatus), `created_at` (timestamptz), `updated_at` (timestamptz)

<!-- pagebreak -->

## ภาคผนวก ก - ตารางและคอลัมน์ (2/3)

รายการคอลัมน์มาจาก `models.py` รวม created_at/updated_at ที่รับจาก TimestampMixin ชนิดและ constraints ฉบับ SQL เต็มอยู่ใน `sql/schema.sql`

### `font_assets` - ไฟล์ฟอนต์อัปโหลดและผลตรวจ

`id` (integer / PK), `organization_id` (integer), `display_name` (varchar(255)), `original_filename` (varchar(255)), `file_path` (varchar(1024)), `sha256` (varchar(64)), `format` (varchar(8)), `supports_thai` (boolean), `status` (enum RecordStatus), `created_at` (timestamptz), `updated_at` (timestamptz)

### `activities` - กิจกรรม ผู้เสนอและผลตัดสิน

`id` (integer / PK), `organization_id` (integer), `department_id` (integer), `activity_name` (varchar(255)), `description` (text / nullable), `activity_date` (date), `activity_type` (varchar(100)), `proposed_by_id` (integer), `approval_status` (enum ActivityStatus), `approved_by_id` (integer / nullable), `approved_at` (timestamptz / nullable), `approval_comment` (text / nullable), `created_at` (timestamptz), `updated_at` (timestamptz)

### `activity_approval_logs` - ประวัติการอนุมัติ/ปฏิเสธแต่ละครั้ง

`id` (integer / PK), `activity_id` (integer), `organization_id` (integer), `decision` (enum ActivityStatus), `decided_by_id` (integer), `comment` (text / nullable), `created_at` (timestamptz)

### `certificate_types` - ประเภทเกียรติบัตรของหน่วยงาน

`id` (integer / PK), `organization_id` (integer / nullable), `name` (varchar(255)), `description` (text / nullable), `status` (enum RecordStatus), `created_at` (timestamptz), `updated_at` (timestamptz)

<!-- pagebreak -->

## ภาคผนวก ก - ตารางและคอลัมน์ (3/3)

รายการคอลัมน์มาจาก `models.py` รวม created_at/updated_at ที่รับจาก TimestampMixin ชนิดและ constraints ฉบับ SQL เต็มอยู่ใน `sql/schema.sql`

### `batches` - งานออกใบกลุ่มและยอดสรุป

`id` (integer / PK), `organization_id` (integer), `template_id` (integer), `topic` (varchar(500)), `issue_date` (date), `event_metadata` (JSONB), `source_type` (varchar(32)), `status` (enum BatchStatus), `total_count` (integer), `success_count` (integer), `failure_count` (integer), `created_by_id` (integer), `activity_id` (integer / nullable), `certificate_type_id` (integer / nullable), `recipient_type` (enum CertificateRecipientType), `external_organization` (varchar(255) / nullable), `created_at` (timestamptz), `updated_at` (timestamptz)

### `batch_rows` - ข้อมูลผู้รับทีละแถวและผลประมวลผล

`id` (integer / PK), `batch_id` (integer), `row_number` (integer), `recipient_reference` (varchar(255) / nullable), `recipient_name` (varchar(255) / nullable), `normalized_data` (JSONB), `status` (varchar(32)), `error_code` (varchar(100) / nullable), `error_message` (text / nullable), `certificate_id` (integer / unique, nullable), `recipient_type` (enum CertificateRecipientType), `external_organization` (varchar(255) / nullable), `created_at` (timestamptz), `updated_at` (timestamptz)

### `certificates` - ข้อมูลใบ รหัสตรวจสอบ ไฟล์ และการเพิกถอน

`id` (integer / PK), `organization_id` (integer), `batch_id` (integer / nullable), `template_id` (integer), `verification_code` (varchar(64)), `recipient_reference` (varchar(255)), `recipient_name` (varchar(255)), `topic` (varchar(500)), `custom_fields` (JSONB), `issue_date` (date), `status` (enum CertificateStatus), `revoked_at` (timestamptz / nullable), `revocation_reason` (text / nullable), `revoked_by_id` (integer / nullable), `image_path` (varchar(1024) / nullable), `idempotency_key` (varchar(255) / nullable), `created_by_id` (integer), `activity_id` (integer / nullable), `certificate_type_id` (integer / nullable), `recipient_type` (enum CertificateRecipientType), `external_organization` (varchar(255) / nullable), `created_at` (timestamptz), `updated_at` (timestamptz)

### `audit_logs` - ประวัติการกระทำต่อข้อมูล

`id` (integer / PK), `actor_id` (integer / nullable), `organization_id` (integer / nullable), `action` (varchar(100)), `target_type` (varchar(100)), `target_id` (varchar(255) / nullable), `correlation_id` (varchar(100) / nullable), `details` (JSONB), `created_at` (timestamptz)

ตาราง `alembic_version` มีคอลัมน์ `version_num` (varchar32, PK) ส่วน `apscheduler_jobs` เป็นตารางของ APScheduler ที่เกิดเมื่อเปิด scheduler และอยู่นอกโมเดล 12 ตารางนี้

<!-- pagebreak -->

## ภาคผนวก ข - ค่าตั้งและตำแหน่ง (1/2)

| ตัวแปร | ค่าหรือแหล่งที่มา | หน้าที่ |
| --- | --- | --- |
| DATABASE_URL | บังคับสำหรับ Python ตรง; entrypoint สร้างให้ใน Docker | URL เชื่อม PostgreSQL เป็นค่าลับ |
| SECRET_KEY | ต้องกำหนดอย่างน้อย32ตัวอักษร | ลงนาม JWT และสร้าง HMAC; ค่าลับ |
| PROJECT_NAME | PKRU Electronic Certificate | ชื่อแอปใน FastAPI |
| POSTGRES_HOST / PGHOST | db | host ฐานข้อมูลสำหรับเว็บ / เครื่องมือสำรอง |
| POSTGRES_PORT | 5432 | พอร์ตที่ entrypoint ใช้ประกอบ URL |
| POSTGRES_USER / PGUSER | certificate_app | ชื่อ role ที่เว็บ / เครื่องมือสำรองใช้ |
| POSTGRES_DB / PGDATABASE | pkru_certificate | ชื่อฐานข้อมูลเดียวกัน |
| POSTGRES_PASSWORD / PGPASSWORD | ต้องกำหนด | รหัส role; Compose ส่งให้แต่ละบริการ |
| PUBLIC_BASE_URL | http://localhost:8000 | ฐาน URL ที่ใช้สร้าง QR |
| APP_BIND_HOST | 127.0.0.1 | interface บน host ที่เปิดรับเว็บ |
| APP_PORT | 8000 | พอร์ตเว็บบน host |
| TEMPLATE_DIR | /app/storage/uploads/templates ใน image | โฟลเดอร์ภาพแม่แบบ |
| CERTIFICATE_DIR | /app/storage/generated/certificates ใน image | โฟลเดอร์ภาพใบ |
| FONT_DIR | /app/storage/uploads/fonts ใน image | โฟลเดอร์ฟอนต์อัปโหลด |
| REQUIRE_RAQM | true ใน Docker / false ใน config | บังคับระบบจัดรูปภาษาไทย |
| ALGORITHM | HS256 | อัลกอริทึม JWT |

ค่าจริงใน `.env.docker` เป็นของแต่ละเครื่อง ตัวแปรที่ config รองรับบางรายการไม่ได้ส่งผ่าน Compose โดยตรง หากต้องเปลี่ยนให้เพิ่ม environment ใน service/override ตามต้องการ

<!-- pagebreak -->

## ภาคผนวก ข - ค่าตั้งและตำแหน่ง (2/2)

| ตัวแปร | ค่าหรือแหล่งที่มา | หน้าที่ |
| --- | --- | --- |
| ACCESS_TOKEN_EXPIRE_MINUTES | 1440 | อายุ token เริ่มต้น24ชั่วโมง |
| LOGIN_PAIR_MAX_FAILURES | 5 | จำนวนผิดสูงสุดต่อชื่อผู้ใช้/IP |
| LOGIN_IP_MAX_FAILURES | 30 | จำนวนผิดสูงสุดรวมต่อ IP |
| LOGIN_WINDOW_SECONDS | 900 | หน้าต่างจำกัดล็อกอิน15นาที |
| LOGIN_CLEANUP_BATCH_SIZE | 100 | จำนวนแถวตัวนับหมดอายุที่ล้างต่อคำขอ |
| AUDIT_RETENTION_DAYS | 365 | อายุ audit log ก่อน scheduler ล้าง |
| ENABLE_SCHEDULER | false | เปิด scheduler ใน lifespan; Docker ใช้ process แยก |
| BACKUP_DIRECTORY | ./backups | โฟลเดอร์ปลายทางบน host |
| BACKUP_TIMEZONE | Asia/Bangkok | เขตเวลารอบสำรอง |
| BACKUP_DAILY_TIME | 03:00 | เวลาสำรองประจำวัน |
| BACKUP_RETAIN_COUNT | 14 | จำนวนชุดสมบูรณ์ที่เก็บ |
| BACKUP_RETRY_SECONDS | 900 | รอลองสำรองใหม่เมื่อผิดพลาด |
| BACKUP_ROOT / BACKUP_STORAGE | /backups / /app/storage | พาธภายในเครื่องมือสำรอง |
| BACKUP_DISPOSABLE_VERIFY | ตั้งเป็น1โดย helper เท่านั้น | guard สำหรับตรวจการกู้คืนชั่วคราว |
| TEST_DATABASE_URL | เฉพาะชุดตรวจของผู้พัฒนา | ฐานข้อมูลที่ fixture/verify_release ใช้ |

ค่าจริงใน `.env.docker` เป็นของแต่ละเครื่อง ตัวแปรที่ config รองรับบางรายการไม่ได้ส่งผ่าน Compose โดยตรง หากต้องเปลี่ยนให้เพิ่ม environment ใน service/override ตามต้องการ

<!-- pagebreak -->

## ภาคผนวก ค - API ทุกเส้นทาง (1/3)

เส้นทางเหล่านี้ประกาศในโค้ดโดยตรง FastAPI ยังมี `/docs`, `/redoc` และ `/openapi.json` สำหรับอ่าน schema ของ API ตามค่าเริ่มต้น

| Method / URL | สิทธิ์ | ฟังก์ชัน / ไฟล์ |
| --- | --- | --- |
| `GET /healthz` | สาธารณะ | `health` / `main.py` |
| `GET /` | สาธารณะ | `public_page` / `main.py` |
| `GET /login` | สาธารณะ | `login_page` / `main.py` |
| `GET /dashboard` | สาธารณะ | `dashboard_page` / `main.py` |
| `POST /activities` | หน่วยงาน | `create_activity` / `routers/activities.py` |
| `GET /activities` | ผู้ล็อกอิน | `list_activities` / `routers/activities.py` |
| `GET /activities/{activity_id}` | ผู้ล็อกอิน | `get_activity` / `routers/activities.py` |
| `POST /activities/{activity_id}/submit` | หน่วยงาน | `submit_activity` / `routers/activities.py` |
| `POST /activities/{activity_id}/approve` | ผู้ล็อกอิน | `approve_activity` / `routers/activities.py` |
| `POST /activities/{activity_id}/reject` | ผู้ล็อกอิน | `reject_activity` / `routers/activities.py` |
| `POST /admin/organizations` | admin | `create_organization` / `routers/admin.py` |
| `GET /admin/organizations` | admin | `list_organizations` / `routers/admin.py` |
| `PATCH /admin/organizations/{organization_id}` | admin | `update_organization` / `routers/admin.py` |
| `GET /admin/organizations/{organization_id}/users` | admin | `list_organization_users` / `routers/admin.py` |
| `POST /admin/organizations/{organization_id}/users` | admin | `create_organization_user_route` / `routers/admin.py` |
| `PATCH /admin/organizations/{organization_id}/users/{user_id}` | admin | `update_organization_user_route` / `routers/admin.py` |
| `POST /admin/organizations/{organization_id}/users/{user_id}/reset-password` | admin | `reset_organization_user_password_route` / `routers/admin.py` |
| `GET /admin/stats` | admin | `get_stats` / `routers/admin.py` |

สิทธิ์ในตารางเป็น dependency หน้าฟังก์ชัน ยังมีเงื่อนไขเจ้าของข้อมูล/สถานะใน service เช่น กิจกรรมของหน่วยงานตนเอง URL แม่แบบและใบภายในไม่ใช่ API สาธารณะ

<!-- pagebreak -->

## ภาคผนวก ค - API ทุกเส้นทาง (2/3)

| Method / URL | สิทธิ์ | ฟังก์ชัน / ไฟล์ |
| --- | --- | --- |
| `GET /admin/audit-logs` | admin | `list_audit_logs` / `routers/admin.py` |
| `POST /login` | สาธารณะ | `login_for_access_token` / `routers/auth.py` |
| `GET /me` | ผู้ล็อกอิน | `read_users_me` / `routers/auth.py` |
| `POST /batches/headers` | หน่วยงาน | `extract_headers` / `routers/batches.py` |
| `POST /batches/preview` | หน่วยงาน | `preview_batch` / `routers/batches.py` |
| `POST /batches` | หน่วยงาน | `create_batch` / `routers/batches.py` |
| `GET /batches` | หน่วยงาน | `list_batches` / `routers/batches.py` |
| `GET /batches/{batch_id}` | หน่วยงาน | `get_batch` / `routers/batches.py` |
| `POST /batches/{batch_id}/process` | หน่วยงาน | `process_confirmed_batch` / `routers/batches.py` |
| `POST /batches/{batch_id}/retry` | หน่วยงาน | `retry_batch_failures` / `routers/batches.py` |
| `GET /batches/{batch_id}/download` | หน่วยงาน | `download_batch` / `routers/batches.py` |
| `GET /certificate-types` | หน่วยงาน | `list_certificate_types` / `routers/certificate_types.py` |
| `POST /certificate-types` | หน่วยงาน | `create_certificate_type` / `routers/certificate_types.py` |
| `PATCH /certificate-types/{type_id}` | หน่วยงาน | `update_certificate_type` / `routers/certificate_types.py` |
| `POST /certificates` | หน่วยงาน | `create_certificate` / `routers/certificates.py` |
| `GET /certificates` | หน่วยงาน | `list_certificates` / `routers/certificates.py` |
| `GET /certificates/{code}/download` | หน่วยงาน | `download_certificate` / `routers/certificates.py` |
| `POST /certificates/{code}/revoke` | หน่วยงาน | `revoke_certificate` / `routers/certificates.py` |

สิทธิ์ในตารางเป็น dependency หน้าฟังก์ชัน ยังมีเงื่อนไขเจ้าของข้อมูล/สถานะใน service เช่น กิจกรรมของหน่วยงานตนเอง URL แม่แบบและใบภายในไม่ใช่ API สาธารณะ

<!-- pagebreak -->

## ภาคผนวก ค - API ทุกเส้นทาง (3/3)

| Method / URL | สิทธิ์ | ฟังก์ชัน / ไฟล์ |
| --- | --- | --- |
| `GET /fonts` | หน่วยงาน | `list_fonts` / `routers/fonts.py` |
| `POST /fonts` | หน่วยงาน | `upload_font` / `routers/fonts.py` |
| `DELETE /fonts/{font_id}` | หน่วยงาน | `deactivate_font` / `routers/fonts.py` |
| `GET /api/public/certificates/{code}/image` | สาธารณะ | `view_certificate` / `routers/public.py` |
| `GET /api/public/certificates/{code}/download` | สาธารณะ | `download_certificate` / `routers/public.py` |
| `GET /api/public/certificates/{code}` | สาธารณะ | `verify_certificate` / `routers/public.py` |
| `POST /templates` | หน่วยงาน | `create_template` / `routers/templates.py` |
| `GET /templates` | หน่วยงาน | `list_templates` / `routers/templates.py` |
| `POST /templates/preview` | หน่วยงาน | `preview_template` / `routers/templates.py` |
| `GET /templates/{template_id}/background` | หน่วยงาน | `get_template_background` / `routers/templates.py` |
| `GET /templates/{template_id}` | หน่วยงาน | `get_template` / `routers/templates.py` |
| `PATCH /templates/{template_id}` | หน่วยงาน | `update_template` / `routers/templates.py` |
| `POST /templates/{template_id}/deactivate` | หน่วยงาน | `deactivate_template` / `routers/templates.py` |
| `DELETE /templates/{template_id}` | หน่วยงาน | `delete_template` / `routers/templates.py` |
| `POST /templates/{template_id}/copy` | หน่วยงาน | `copy_template` / `routers/templates.py` |

สิทธิ์ในตารางเป็น dependency หน้าฟังก์ชัน ยังมีเงื่อนไขเจ้าของข้อมูล/สถานะใน service เช่น กิจกรรมของหน่วยงานตนเอง URL แม่แบบและใบภายในไม่ใช่ API สาธารณะ

<!-- pagebreak -->

## ภาคผนวก ง - บัญชีไฟล์: ไฟล์รากและเอกสาร (1/9)

แจกแจงซอร์สที่ Git ติดตามครบ 147 ไฟล์ ณ commit `3c6642f` โดยแยกชุดตรวจออกจาก runtime

| ไฟล์จากรากโปรเจกต์ | หน้าที่ |
| --- | --- |
| `.dockerignore` | ตัดไฟล์ลับ ข้อมูลจริง tests และเอกสารออกจากบริบท build ที่ไม่ต้องใช้ |
| `.env.docker.example` | ตัวอย่างค่าตั้ง Compose, PostgreSQL, login limit และ backup |
| `.env.example` | ตัวอย่าง environment สำหรับรัน Python โดยตรง ไม่มีรหัสจริง |
| `.gitignore` | กำหนดไฟล์ที่ Git ไม่ติดตาม เช่น env, storage, backups และ cache |
| `Dockerfile` | สร้าง image เว็บ Python พร้อมตัวจัดรูปภาษาไทยและ healthcheck |
| `README.md` | คู่มือเริ่มใช้งาน เลือก SQL หลัก และคำสั่งประจำ |
| `alembic.ini` | ค่าตั้ง Alembic และตำแหน่งประวัติ migration |
| `auth.py` | hash รหัสผ่าน สร้าง/ตรวจ JWT และ dependencies ตรวจบทบาท |
| `compose.yaml` | ประกอบ db, migrate, web, backup และ scheduler พร้อม volumes |
| `config.py` | โมเดล Settings ตรวจค่า environment และค่าปริยายของแอป |
| `core/__init__.py` | ประกาศแพ็กเกจ core |
| `core/errors.py` | สร้าง error envelope และ handlers ของ HTTP/validation/ข้อผิดพลาดภายใน |
| `database.py` | สร้าง engine, SessionLocal, Base และ session ต่อคำขอ ใช้ public |
| `docs/deployment.md` | คู่มือดูแล ติดตั้ง อัปเกรด สำรอง กู้คืน และ HTTPS |
| `main.py` | จุดเริ่ม FastAPI รวม router เสิร์ฟหน้า/static และตรวจ startup/health |
| `models.py` | ORM ของ 12 ตาราง พร้อม enum, key, index และความสัมพันธ์ |
| `repositories/__init__.py` | ประกาศแพ็กเกจ repositories |
| `repositories/scoping.py` | สร้าง query ที่จำกัด organization_id ตามบทบาทผู้ใช้ |
| `requirements-dev.txt` | รวม runtime และเพิ่มไลบรารีสำหรับตรวจโค้ด |
| `requirements.txt` | ตรึงไลบรารีที่ใช้ขณะรันเว็บ |
| `tasks.py` | APScheduler และงานล้าง audit log เก่า |

<!-- pagebreak -->

## ภาคผนวก ง - บัญชีไฟล์: Router และ schema (2/9)

แจกแจงซอร์สที่ Git ติดตามครบ 147 ไฟล์ ณ commit `3c6642f` โดยแยกชุดตรวจออกจาก runtime

| ไฟล์จากรากโปรเจกต์ | หน้าที่ |
| --- | --- |
| `routers/activities.py` | API สร้าง/ส่ง/ดู/อนุมัติ/ปฏิเสธกิจกรรม |
| `routers/admin.py` | API จัดการหน่วยงาน บุคลากร รหัสผ่าน สถิติ และ audit |
| `routers/auth.py` | POST /login, GET /me และเรียกบริการจำกัดการเดารหัส |
| `routers/batches.py` | API อ่านหัวตาราง พรีวิว ยืนยัน ประมวลผล ลองใหม่ และ ZIP |
| `routers/certificate_types.py` | API จัดประเภทเกียรติบัตรของหน่วยงาน |
| `routers/certificates.py` | API ออก ค้นหา ดาวน์โหลด และเพิกถอนใบในหน่วยงาน |
| `routers/fonts.py` | API รายการ/อัปโหลด/ปิดใช้ฟอนต์ พร้อมตรวจการอ้างอิง |
| `routers/public.py` | API สาธารณะค้นด้วยรหัส เปิดภาพ และแปลงดาวน์โหลด |
| `routers/templates.py` | API อัปโหลด พรีวิว แก้ไข ทำสำเนา ปิดใช้ และลบแม่แบบ |
| `schemas/__init__.py` | รวม export โมเดลข้อมูลที่โมดูลอื่นนำไปใช้ |
| `schemas/activities.py` | ข้อมูลสร้างกิจกรรม ผลตอบกลับ และความคิดเห็นตัดสิน |
| `schemas/batches.py` | ข้อมูลชุดงาน แถว ผลสรุป และลายเซ็นขณะประมวลผล |
| `schemas/certificate_types.py` | ข้อมูลชื่อ รายละเอียด สถานะ และผลตอบกลับประเภทใบ |
| `schemas/certificates.py` | ฟิลด์ออกใบ ผู้รับ ลายเซ็น เพิกถอน และผลตอบกลับ |
| `schemas/common.py` | โครงสร้าง error ที่ใช้ร่วมกัน |
| `schemas/fonts.py` | ผลตอบกลับฟอนต์ระบบและฟอนต์อัปโหลด |
| `schemas/organizations.py` | ข้อมูลหน่วยงาน ข้อมูลติดต่อ และบัญชีแรก |
| `schemas/signatures.py` | PNG data URL ชื่อและตำแหน่งผู้ลงนาม |
| `schemas/template_layout.py` | ตรวจ layout version2 พิกัด styles วันที่ และชนิดช่อง |
| `schemas/templates.py` | ข้อมูลสร้าง/แก้/อ่านแม่แบบ รวมสถานะต้นฉบับและลบ |
| `schemas/users.py` | ข้อมูลสร้างบัญชี เพิ่มบุคลากร เปิด/ปิดและตั้งรหัสใหม่ |

<!-- pagebreak -->

## ภาคผนวก ง - บัญชีไฟล์: Services (3/9)

แจกแจงซอร์สที่ Git ติดตามครบ 147 ไฟล์ ณ commit `3c6642f` โดยแยกชุดตรวจออกจาก runtime

| ไฟล์จากรากโปรเจกต์ | หน้าที่ |
| --- | --- |
| `services/accounts.py` | กฎสร้างหน่วยงาน/ผู้ใช้ แก้ข้อมูล สถานะ และรหัสผ่าน |
| `services/archive_builder.py` | สร้าง ZIP ชื่อไฟล์ปลอดภัย และ manifest.csv ของผลลัพธ์ |
| `services/audit.py` | เพิ่ม AuditLog ใน transaction พร้อมปิดบังค่าลับตามชื่อ key |
| `services/batch_service.py` | ตรวจแถว ยืนยันงาน claim/retry ประมวลผล และสรุปจำนวน |
| `services/builtin_templates.py` | อ่านต้นฉบับที่ฝังมาและสร้าง/คืนรายการให้แต่ละหน่วยงาน |
| `services/certificates.py` | กฎออกใบ idempotency รหัสไม่ซ้ำ การกู้สถานะ commit และเพิกถอน |
| `services/font_files.py` | ตรวจ TTF/OTF อักษรไทย checksum และเก็บไฟล์ฟอนต์ |
| `services/font_registry.py` | เลือกพาธฟอนต์ระบบหรือฟอนต์หน่วยงานตาม FontReference |
| `services/image_builder.py` | ประกอบภาพพื้นหลัง ข้อความ วันที่ ลายเซ็น QR และ ID เป็น PNG |
| `services/import_parser.py` | อ่าน Excel ตรวจ ZIP/XML และแปลงรูปแบบเซลล์/จับคู่ข้อมูล |
| `services/login_throttle.py` | HMAC keys, row locks, ตัวนับหมดอายุ และ Retry-After |
| `services/organization_identity.py` | ปรับรูปแบบชื่อหน่วยงานและสร้างรหัสหน่วยงาน |
| `services/signatures.py` | ตรวจ/เตรียม PNG โปร่งใสและจับคู่ลายเซ็นกับ element ID |
| `services/storage_paths.py` | แก้พาธที่จัดการไว้ ตรวจขอบเขต storage และไฟล์เก่า |
| `services/template_files.py` | ตรวจ PNG/JPEG และเก็บภาพพื้นหลังด้วยชื่อเฉพาะ |
| `services/template_layout.py` | normalize layout ดึงฟิลด์กำหนดเองและแปลง positions เก่า |
| `services/text_layout.py` | RAQM การตัดคำ/วางบรรทัด และพฤติกรรมข้อความล้นบล็อก |
| `services/value_formatter.py` | แปลงค่าฟิลด์ วันที่ เวลา ช่วงวัน/เวลา และปี พ.ศ./ค.ศ. |

<!-- pagebreak -->

## ภาคผนวก ง - บัญชีไฟล์: สคริปต์ Docker และ SQL (4/9)

แจกแจงซอร์สที่ Git ติดตามครบ 147 ไฟล์ ณ commit `3c6642f` โดยแยกชุดตรวจออกจาก runtime

| ไฟล์จากรากโปรเจกต์ | หน้าที่ |
| --- | --- |
| `alembic/README` | อธิบายโฟลเดอร์ migration |
| `alembic/env.py` | เชื่อม metadata กับ Alembic และตั้ง schema public |
| `alembic/script.py.mako` | แม่แบบไฟล์ migration ที่ Alembic สร้าง |
| `alembic/versions/98f31a2c7d44_template_layout_and_fonts.py` | เพิ่ม layout แม่แบบและข้อมูลฟอนต์หน่วยงาน |
| `alembic/versions/a2b3c4d5e6f7_template_deletion.py` | เพิ่ม deleted_at สำหรับลบแม่แบบโดยคงข้อมูลอ้างอิง |
| `alembic/versions/a7c1e9f4b2d8_unique_organization_name.py` | กำหนดชื่อหน่วยงานที่ปรับรูปแบบแล้วไม่ซ้ำ |
| `alembic/versions/b6c7d8e9f0a1_login_attempt_buckets.py` | เพิ่มตารางตัวนับล็อกอินร่วมกันข้ามคำขอ/worker |
| `alembic/versions/cbef4c6ea711_organization_certificate_schema.py` | สร้างฐานโครงสร้างหน่วยงาน ผู้ใช้ แม่แบบ งานกลุ่ม ใบ และ audit |
| `alembic/versions/d4e5f6a7b8c9_activity_certificate_types.py` | เพิ่มกิจกรรม ประวัติอนุมัติ ประเภทใบ และข้อมูลผู้รับ |
| `alembic/versions/f1a2b3c4d5e6_builtin_templates.py` | เพิ่มตัวระบุแม่แบบต้นฉบับประจำหน่วยงาน |
| `docker/backup.Dockerfile` | สร้าง image สำรองด้วย PostgreSQL18 client, Python และ timezone data |
| `scripts/backup.ps1` | คำสั่ง Windows สำรอง ดูสถานะ และสร้างคอนเทนเนอร์กู้คืนชั่วคราว |

<!-- pagebreak -->

## ภาคผนวก ง - บัญชีไฟล์: สคริปต์ Docker และ SQL (5/9)

แจกแจงซอร์สที่ Git ติดตามครบ 147 ไฟล์ ณ commit `3c6642f` โดยแยกชุดตรวจออกจาก runtime

| ไฟล์จากรากโปรเจกต์ | หน้าที่ |
| --- | --- |
| `scripts/backup.py` | สร้าง/ตรวจ/กู้ชุดสำรอง ตารางรายวัน retention และ health |
| `scripts/container_entrypoint.py` | ประกอบ DATABASE_URL จาก environment แล้วเริ่มคำสั่งหลัก |
| `scripts/create_admin.py` | สร้างผู้ดูแลคนแรก รับรหัสผ่านผ่าน prompt หรือ env ที่กำหนด |
| `scripts/docker.ps1` | คำสั่งสั้น start/stop/restart/status/logs สำหรับ Compose |
| `scripts/export_schema.py` | สร้าง SQL หลักจาก metadata และ revision ปัจจุบัน |
| `scripts/initialize_database.py` | bootstrap SQL เมื่อว่างและรัน migration ด้วย lock |
| `scripts/run_scheduler.py` | เริ่ม scheduler เป็น process แยกและจัดการสัญญาณปิด |
| `scripts/seed_demo.py` | สร้างข้อมูลตัวอย่างเมื่อเรียกเอง ไม่ใช่งานเริ่มระบบจริง |
| `scripts/verify_release.ps1` | ตัวเรียกชุดตรวจ release สำหรับ PowerShell |
| `scripts/verify_release.py` | รวมคำสั่งตรวจ migration, Python, JS, dependency และภาษาไทย |
| `sql/schema.sql` | SQL รวมสำหรับฐานข้อมูลใหม่ที่ว่าง พร้อมเวอร์ชัน Alembic |

<!-- pagebreak -->

## ภาคผนวก ง - บัญชีไฟล์: หน้าเว็บและ assets (6/9)

แจกแจงซอร์สที่ Git ติดตามครบ 147 ไฟล์ ณ commit `3c6642f` โดยแยกชุดตรวจออกจาก runtime

| ไฟล์จากรากโปรเจกต์ | หน้าที่ |
| --- | --- |
| `assets/templates/original/background.webp` | ภาพพื้นหลังของแม่แบบต้นฉบับที่มากับระบบ |
| `assets/templates/original/template.json` | ชื่อ ขนาด และตำแหน่งช่องของแม่แบบต้นฉบับ |
| `static/css/dashboard.css` | รูปแบบ Dashboard, editor, dialog และหน้าจอมือถือ |
| `static/css/style.css` | รูปแบบร่วม หน้า login และหน้าตรวจสอบ |
| `static/dashboard.html` | โครงหน้าจอผู้ดูแล/หน่วยงาน แบบฟอร์ม และ dialogs |
| `static/fonts/LICENSE-THNiramitAS.txt` | ใบอนุญาตฟอนต์ TH Niramit AS |
| `static/fonts/LICENSES.md` | สรุปที่มาฟอนต์และเงื่อนไขใช้งาน |
| `static/fonts/NotoSansThai-Bold.ttf` | ฟอนต์ระบบ Noto Sans Thai น้ำหนักหนา |
| `static/fonts/NotoSansThai-Regular.ttf` | ฟอนต์ระบบ Noto Sans Thai น้ำหนักปกติ |
| `static/fonts/OFL-NotoSansThai.txt` | ใบอนุญาต SIL OFL ของ Noto Sans Thai |
| `static/fonts/OFL-Sarabun.txt` | ใบอนุญาต SIL OFL ของ Sarabun |
| `static/fonts/Sarabun-Bold.ttf` | ฟอนต์ระบบ Sarabun น้ำหนักหนา |

<!-- pagebreak -->

## ภาคผนวก ง - บัญชีไฟล์: หน้าเว็บและ assets (7/9)

แจกแจงซอร์สที่ Git ติดตามครบ 147 ไฟล์ ณ commit `3c6642f` โดยแยกชุดตรวจออกจาก runtime

| ไฟล์จากรากโปรเจกต์ | หน้าที่ |
| --- | --- |
| `static/fonts/Sarabun-Regular.ttf` | ฟอนต์ระบบ Sarabun น้ำหนักปกติ |
| `static/fonts/THNiramitAS-Bold.ttf` | ฟอนต์ระบบ TH Niramit AS น้ำหนักหนา |
| `static/fonts/THNiramitAS-Regular.ttf` | ฟอนต์ระบบ TH Niramit AS น้ำหนักปกติ |
| `static/index.html` | โครงหน้าเว็บค้นหา/ตรวจสอบเกียรติบัตรสาธารณะ |
| `static/js/api-client.js` | fetch, token ใน localStorage, error และการดาวน์โหลด |
| `static/js/auth.js` | เหตุการณ์หน้า login และกลับเข้าสู่ session เดิม |
| `static/js/dashboard.js` | ควบคุมเมนู โหลดข้อมูล ออกใบ editor และคำสั่งจัดการหลัก |
| `static/js/main.js` | ค้นหารหัส แสดงรายละเอียด/ภาพ และดาวน์โหลดในหน้าสาธารณะ |
| `static/js/organization-admin.js` | กฎ dialog และ request สำหรับหน่วยงาน/บุคลากร |
| `static/js/signature-pad.js` | วาด/อ่าน PNG ตรวจเส้นและเตรียมข้อมูลผู้ลงนาม |
| `static/js/template-layout.js` | พิกัด canvas ฟิลด์แม่แบบ จัดกลางและ snapping |
| `static/login.html` | โครงหน้าเข้าสู่ระบบ |

<!-- pagebreak -->

## ภาคผนวก ง - บัญชีไฟล์: ชุดตรวจของผู้พัฒนา (8/9)

แจกแจงซอร์สที่ Git ติดตามครบ 147 ไฟล์ ณ commit `3c6642f` โดยแยกชุดตรวจออกจาก runtime

| ไฟล์จากรากโปรเจกต์ | หน้าที่ |
| --- | --- |
| `tests/conftest.py` | fixtures ฐานข้อมูลและพื้นที่ชั่วคราวสำหรับ pytest |
| `tests/fixtures/recipients.csv` | fixture CSV ของชุดตรวจเดิม ไม่ใช่ช่องทางนำเข้าบนเว็บ |
| `tests/fixtures/recipients.xlsx` | ไฟล์ Excel ตัวอย่างสำหรับตรวจตัวอ่านข้อมูล |
| `tests/js/organization-admin.test.js` | ตรวจการแยกงานสร้าง/แก้หน่วยงานและบุคลากร |
| `tests/js/signature-editors.browser.cjs` | ตรวจหน้าต่างลายเซ็นในเบราว์เซอร์ |
| `tests/js/signature-flows.test.js` | ตรวจส่งหลายลายเซ็นและ idempotency ฝั่งหน้าเว็บ |
| `tests/js/signature-pad.test.js` | ตรวจ PNG ความโปร่งใส เส้น และพิกัดวาด |
| `tests/js/template-alignment.browser.cjs` | ตรวจการจัดตำแหน่งแม่แบบผ่านเบราว์เซอร์ |
| `tests/js/template-layout.test.js` | ตรวจพิกัดฟิลด์ ซูม จัดกลาง และเส้นไกด์ |
| `tests/test_activities_api.py` | ตรวจ API กิจกรรม สิทธิ์และลำดับอนุมัติ |
| `tests/test_admin_organizations.py` | ตรวจ หน่วยงาน บุคลากร และสิทธิ์ admin |
| `tests/test_auth.py` | ตรวจ การล็อกอิน JWT บัญชีและการจำกัดคำขอ |
| `tests/test_backups.py` | ตรวจ archive checksum retention เป้าหมายกู้คืนและ retry |
| `tests/test_batches.py` | ตรวจ ยืนยัน/ประมวลผลกลุ่มและการลองแถวล้มเหลวใหม่ |
| `tests/test_certificate_service.py` | ตรวจ กฎออกใบ transaction และ idempotency |
| `tests/test_certificate_types_api.py` | ตรวจ API ประเภทเกียรติบัตร |
| `tests/test_certificates_api.py` | ตรวจ ออกใบ ดาวน์โหลด และเพิกถอนผ่าน API |
| `tests/test_configuration.py` | ตรวจ validation ของค่าตั้งระบบ |
| `tests/test_container_startup.py` | ตรวจ การเตรียมคำสั่งและค่าตั้งในคอนเทนเนอร์ |
| `tests/test_end_to_end.py` | ตรวจ เส้นทางการทำงานหลายส่วนร่วมกัน |

<!-- pagebreak -->

## ภาคผนวก ง - บัญชีไฟล์: ชุดตรวจของผู้พัฒนา (9/9)

แจกแจงซอร์สที่ Git ติดตามครบ 147 ไฟล์ ณ commit `3c6642f` โดยแยกชุดตรวจออกจาก runtime

| ไฟล์จากรากโปรเจกต์ | หน้าที่ |
| --- | --- |
| `tests/test_font_files.py` | ตรวจ ชนิดฟอนต์ ขนาดและอักษรที่รองรับ |
| `tests/test_fonts_api.py` | ตรวจ อัปโหลด เลือกและปิดใช้ฟอนต์ผ่าน API |
| `tests/test_health.py` | ตรวจ health endpoint และกรณีฐานข้อมูลขัดข้อง |
| `tests/test_import_parser.py` | ตรวจ Excel หัวตาราง รูปแบบเซลล์และข้อมูลผิดรูป |
| `tests/test_login_throttle.py` | ตรวจ ตัวนับเดารหัส ช่วงเวลาและ response429 |
| `tests/test_models.py` | ตรวจ ตาราง constraints และความสัมพันธ์ |
| `tests/test_organization_identity.py` | ตรวจ ปรับชื่อและสร้างรหัสหน่วยงาน |
| `tests/test_organization_migration.py` | ตรวจ migration เกี่ยวกับข้อมูลหน่วยงาน |
| `tests/test_pages.py` | ตรวจ หน้า HTML และการเสิร์ฟไฟล์ static |
| `tests/test_public_verification.py` | ตรวจ ค้นหารหัส ภาพ ดาวน์โหลดและสถานะเพิกถอนสาธารณะ |
| `tests/test_qr_labels.py` | ตรวจ รหัสใต้ QR การวางและขนาด |
| `tests/test_scheduler.py` | ตรวจ การเริ่ม/หยุดและล้าง audit log ตามอายุ |
| `tests/test_scoping.py` | ตรวจ การจำกัดข้อมูลตามหน่วยงาน |
| `tests/test_seed_scripts.py` | ตรวจ CLI สร้าง admin และข้อมูลตัวอย่าง |
| `tests/test_signature_rendering.py` | ตรวจ ภาพลายเซ็น ชื่อ ตำแหน่งและการจัดวาง |
| `tests/test_signatures.py` | ตรวจ ตรวจ PNG และจับคู่หลายผู้ลงนาม |
| `tests/test_template_layout.py` | ตรวจ schema และการ normalize layout |
| `tests/test_templates.py` | ตรวจ API แม่แบบ ต้นฉบับ สำเนาและการลบ |
| `tests/test_thai_layout.py` | ตรวจ การจัดรูปภาษาไทยและข้อความหลายบรรทัด |
| `tests/test_value_formatter.py` | ตรวจ วันที่ เวลา ช่วงค่าและรูปแบบแสดงผล |

<!-- pagebreak -->

## ภาคผนวก จ - ไฟล์ที่เกิดระหว่างทำงานและวงจรดูแล

| รายการ | การจัดการ |
| --- | --- |
| `.env` / `.env.docker` | ค่าตั้งเฉพาะเครื่องและค่าลับ Git ไม่ติดตาม |
| `storage/` หรือ volume certificate_storage | ภาพพื้นหลัง ฟอนต์อัปโหลด และใบที่สร้าง ต้องสำรองคู่ฐานข้อมูล |
| `backups/auto-.../database.dump` | ข้อมูลและโครงสร้าง public จาก pg_dump |
| `backups/auto-.../storage.tar.gz` | ไฟล์ใน storage ชุดเดียวกับ snapshot ฐานข้อมูล |
| `backups/auto-.../manifest.json` | จำนวนแถว รายการอ้างอิงและ checksum |
| `restore-verification.json` | ผลกู้คืนของชุดนั้นเมื่อรัน verify |
| `backups/status.json` | สถานะความสำเร็จ/ล้มเหลวล่าสุดของตัวสำรอง |
| `.venv/`, `venv/`, `env/` | ไลบรารี Python บนเครื่องพัฒนา สร้างใหม่จาก requirements ได้ |
| `__pycache__/`, `.pytest_cache/` | cache ของ Python และ pytest ไม่ใช่ข้อมูลผู้ใช้ |
| `.git/` | ประวัติและสถานะ Git ไม่ใช่ไฟล์ที่เว็บเสิร์ฟ |
| `test-artifacts/`, `.worktrees/` | พื้นที่ผลตรวจหรือ checkout ที่เครื่องมือพัฒนาอาจสร้าง |
| `static/uploads/`, `static/generated/`, `static/certificates/` | พาธเก่าที่ถูก ignore และปิดการเข้าถึงโดย StaticFiles; runtime ใหม่ใช้ storage |
| `docs/project-guide.md`, `docs/project-guide.pdf` | คู่มือฉบับนี้ที่เพิ่มในโปรเจกต์ |

### วงจรอัปเกรดที่ใช้อยู่

สำรองพร้อมตรวจการกู้คืน → หยุด web/scheduler/backup → แก้ซอร์ส → build image → รัน migrate ให้จบ0 → เปิดบริการ → ตรวจ health และใบเดิม ขั้นตอนละเอียดอยู่ใน `docs/deployment.md`

เมื่อแก้ schema ให้ปรับ `models.py`, เพิ่ม migration และอัปเดต `sql/schema.sql` คู่กัน การล้างข้อมูลฐานข้อมูลไม่ได้ลบ assets ที่ฝังใน image แต่การเก็บใบเก่าต้องมีทั้งแถวฐานข้อมูลและไฟล์ storage

### ขอบเขตเอกสาร

คู่มือนี้อธิบายสิ่งที่พบในโค้ดและไฟล์ของโปรเจกต์ ไม่ใช่การเพิ่มฟีเจอร์ใหม่ ตาราง API/คอลัมน์ดึงจาก AST และรายการไฟล์เทียบ Git เพื่อไม่ตกหล่น ไฟล์ติดตั้งไลบรารีและไฟล์ผู้ใช้ที่เปลี่ยนทุกวันอธิบายเป็นหมวด การระบุชื่อค่าลับบอกหน้าที่โดยไม่มีค่ารหัสผ่านจริง
