# PKRU Electronic Certificate

## Cloud deployment: Koyeb + Supabase

Start with the [step-by-step Koyeb + Supabase guide](docs/koyeb-supabase.md). Koyeb builds this repository using `Dockerfile`; Supabase provides PostgreSQL and private file storage. Use `python scripts/start_cloud.py` as the cloud run command. The Docker Compose instructions below are for local/VPS installation with PostgreSQL 18; Supabase PostgreSQL 17.6 was tested separately.


ระบบออกและตรวจสอบเกียรติบัตรภาษาไทย ใช้ FastAPI และ PostgreSQL รองรับหลายหน่วยงาน ออกเกียรติบัตรรายบุคคล/นำเข้าจาก Excel ตรวจสอบด้วย QR และดาวน์โหลด PNG, JPG, PDF

อ่านภาพรวมการทำงาน เทคโนโลยี หน้าจอ ฐานข้อมูล API และหน้าที่ไฟล์ทั้งหมดได้ใน **[คู่มือระบบและโครงสร้างโปรเจกต์](docs/project-guide.md)** หรือ **[ฉบับ PDF](docs/project-guide.pdf)**

## ใช้ไฟล์ไหน

| รายการ | ไฟล์หรือค่าที่ใช้จริง |
| --- | --- |
| เปิดระบบ Docker | **`compose.yaml`** |
| ตั้งค่า Docker | **`.env.docker`** คัดลอกจาก `.env.docker.example` |
| ระบบฐานข้อมูล | **PostgreSQL 18** |
| ชื่อฐานข้อมูลหลัก | **`pkru_certificate`** |
| Schema ที่ใช้ | **`public` เท่านั้น** |
| SQL สำหรับสร้างฐานข้อมูลใหม่ | **[`sql/schema.sql`](sql/schema.sql)** |
| โครงสร้างข้อมูลที่โค้ดใช้ | `models.py` |
| ประวัติอัปเกรดฐานข้อมูลเดิม | `alembic/` ระบบเรียกให้อัตโนมัติ |

**ใช้ Docker และฐานข้อมูลใช้งานจริงชุดเดียวตามตารางนี้** `web`, `migrate`, `scheduler` และ `backup` เชื่อมต่อฐานข้อมูลเดียวกัน ไฟล์ตรวจโค้ดใน `tests/` ไม่เกี่ยวกับการเปิดเว็บและไม่ถูกบรรจุใน Docker image

`sql/schema.sql` มีตารางแอป 12 ตาราง รวมคีย์ ความสัมพันธ์ ดัชนี และตาราง `alembic_version` สำหรับระบุเวอร์ชัน ไม่มีบัญชีหรือข้อมูลตัวอย่าง ใช้สร้างฐานข้อมูลใหม่ที่ว่างเท่านั้น

## เปิดระบบ

1. เปิด Docker Desktop ให้ Linux engine ทำงาน แล้วเปิด PowerShell ในโฟลเดอร์โปรเจกต์
2. คัดลอกไฟล์ตั้งค่าครั้งแรก:

   ```powershell
   Copy-Item .env.docker.example .env.docker
   ```

   หากมี `.env.docker` แล้วให้แก้ไฟล์เดิม ไม่คัดลอกทับ

3. สร้างค่าสุ่มสองครั้ง แล้วนำค่าคนละชุดไปใส่ `POSTGRES_PASSWORD` และ `SECRET_KEY`:

   ```text
   docker run --rm python:3.13-slim-bookworm python -c "import secrets; print(secrets.token_hex(32))"
   ```

   ค่าอื่นใน `.env.docker`:

   | ค่า | ค่าเริ่มต้น / วิธีตั้ง |
   | --- | --- |
   | `POSTGRES_USER` | `certificate_app` |
   | `POSTGRES_DB` | `pkru_certificate` |
   | `PUBLIC_BASE_URL` | URL ที่ผู้รับเข้าถึงได้ ใช้สร้าง QR; เมื่อเปลี่ยนค่าและสร้าง web container ใหม่ ระบบจะอัปเดต QR ในภาพที่เก็บไว้ |
   | `QR_UPDATE_ON_STARTUP` | `true` อัปเดต QR เบื้องหลังเมื่อเว็บเริ่มทำงาน; ใช้ `false` หากต้องการตรวจ dry-run ก่อน |
   | `APP_BIND_HOST` | `127.0.0.1` ใช้เฉพาะเครื่องนี้ หรือ `0.0.0.0` เมื่อต้องเปิดให้เครือข่ายเข้าถึง |
   | `APP_PORT` | `8000` |

4. เปิดระบบ:

   ```text
   docker compose --env-file .env.docker up -d --build
   docker compose --env-file .env.docker ps -a
   ```

   ครั้งแรก `migrate` จะอ่าน **`sql/schema.sql`** เพื่อสร้างตาราง แล้วตรวจเวอร์ชันด้วย Alembic ถ้าฐานข้อมูลมีอยู่แล้วจะใช้ประวัติ migration เพื่ออัปเกรด โดยไม่รัน SQL สร้างตารางซ้ำ

   `db` และ `web` ควรขึ้น `healthy` และ `backup` จะขึ้น `healthy` หลังสำรองครั้งแรกสำเร็จ ส่วน `migrate` ขึ้น `Exited (0)` เมื่อเตรียมฐานข้อมูลเสร็จ

5. สร้างผู้ดูแลครั้งแรก โดยคำสั่งจะถามรหัสผ่าน:

   ```text
   docker compose --env-file .env.docker run --rm --no-deps web python scripts/create_admin.py --username admin
   ```

6. เปิด [หน้าเว็บ](http://localhost:8000) หรือ [เข้าสู่ระบบ](http://localhost:8000/login)

ถ้ากู้คืนข้อมูลเดิมมาแล้ว ให้ใช้บัญชีเดิมได้เลย การเปิด Docker ครั้งแรกยังไม่ย้ายข้อมูลจาก PostgreSQL บนเครื่องเดิมให้อัตโนมัติ ดู [คู่มือย้ายและสำรองข้อมูล](docs/deployment.md)

## ใช้ SQL โดยตรง

สำหรับ PostgreSQL ที่ติดตั้งเอง ให้สร้างฐานข้อมูลว่างชื่อ `pkru_certificate` แล้วเปิด **[`sql/schema.sql`](sql/schema.sql)** รันในฐานข้อมูลนั้นครั้งเดียว เช่นใช้ Query Tool ของ pgAdmin หรือ:

```text
psql -h 127.0.0.1 -U certificate_app -d pkru_certificate -v ON_ERROR_STOP=1 -f sql/schema.sql
```

ไฟล์นี้มี transaction ครอบไว้ หากมีตารางเดิมแล้วจะไม่ทับข้อมูล ให้ใช้การอัปเกรดผ่าน `migrate` แทน ห้ามนำ SQL สร้างฐานข้อมูลใหม่ไปใช้แทนไฟล์สำรองข้อมูล

ผู้ใช้ Docker ใช้คำสั่งเปิดระบบด้านบนได้เลย ระบบอ่าน SQL ให้เอง ส่วน `.env.example` เป็นตัวอย่างสำหรับรัน Python โดยตรง การตั้งค่า Docker ใช้ `.env.docker`

## ใช้งานแม่แบบ

- **ต้นฉบับ** เป็นแม่แบบประจำระบบ เก็บภาพพื้นหลังและตำแหน่งช่องไว้ใน `assets/templates/original/` ซึ่งรวมอยู่ใน Docker image
- หน่วยงานใหม่ได้รับแม่แบบต้นฉบับทันที ระบบตรวจและสร้างกลับให้เมื่อเปิดเว็บหรือโหลดรายการแม่แบบ จึงยังใช้ได้หลังล้างข้อมูลและสร้างหน่วยงานใหม่ โดยไม่ต้องอัปโหลดภาพซ้ำ
- ต้นฉบับของระบบลบ ปิดใช้งาน หรือแก้ทับไม่ได้ กด **ทำสำเนาเพื่อแก้ไข** เพื่อปรับชื่อ ตำแหน่ง และสีสำหรับหน่วยงานของตน
- แม่แบบที่สร้างเองและสำเนามีปุ่ม **ลบแม่แบบ** ทั้งรายการที่ใช้งานและปิดใช้งาน เมื่อลบจะหายจากรายการและใช้กับใบใหม่ไม่ได้ เกียรติบัตรเดิมยังดาวน์โหลดและตรวจสอบได้
- การออกแบบกลุ่มนำเข้าจาก **ไฟล์ Excel (.xlsx)** แล้วจับคู่คอลัมน์และตรวจข้อมูลก่อนยืนยัน
- ลาก/ปรับขนาดข้อความ วันที่ QR และลายเซ็นได้
- ข้อความทั่วไป วันที่ และข้อความใต้ลายเซ็นแสดงเลยขอบบล็อกได้โดยคงขนาดตัวอักษรและการจัดแนว ส่วนช่องข้อความหลายบรรทัดตัดบรรทัดตามความกว้างและต่อเลยขอบล่างได้ ภาพตัวอย่างและใบที่ออกจริงตัดข้อความเฉพาะส่วนที่เลยขอบหน้ากระดาษ
- จัดกึ่งกลางแนวนอน แนวตั้ง หรือทั้งหน้า พร้อมเส้นไกด์ขณะลาก กด Alt เพื่อขยับอิสระ
- เพิ่มลายเซ็นหลายบล็อก แยกภาพลายเซ็น ชื่อ และตำแหน่งของแต่ละคน รองรับวาดหรืออัปโหลด PNG
- เมื่อเพิ่ม QR ระบบเพิ่มบล็อกข้อความ **ID เกียรติบัตร** ไว้ด้านล่างให้อัตโนมัติ รหัสดึงจากเกียรติบัตรแต่ละใบ
- เลือกบล็อก **ID เกียรติบัตร** เพื่อปรับสี ฟอนต์ ขนาด และตำแหน่ง ข้อความมีพื้นหลังโปร่งใส และย่อให้พอดีกรอบเมื่อจำเป็น
- กรอบเล็กมากทำให้ตัวอักษรและ QR อ่านได้จำกัดตามจำนวนพิกเซล ควรเลือกขนาดที่มองเห็นและสแกนได้เมื่อพิมพ์จริง
- การปรับรูปแบบมีผลกับพรีวิวและเกียรติบัตรที่ออกใหม่ ภาพที่ออกแล้วคงเดิม

## คำสั่งที่ใช้ประจำ

### การเข้าสู่ระบบ

กรอกรหัสผิดครบ **5 ครั้งต่อชื่อผู้ใช้และ IP ภายใน 15 นาที** จะต้องรอให้ครบช่วงเวลาก่อนลองอีกครั้ง ส่วนการลองหลายชื่อจาก IP เดียวกันจำกัดรวม **30 ครั้งใน 15 นาที** หน้าเว็บแสดงข้อความให้รอและ API ส่ง `429 AUTH_RATE_LIMITED` พร้อม `Retry-After` การเข้าสู่ระบบสำเร็จล้างเฉพาะจำนวนครั้งของชื่อผู้ใช้นั้น ไม่ล้างยอดรวมของ IP

ระบบเก็บตัวนับใน `public.login_attempt_buckets` โดยใช้ HMAC แทนชื่อผู้ใช้และ IP จึงจำกัดได้ต่อเนื่องข้าม worker และการรีสตาร์ต และไม่มีการเก็บรหัสผ่านไว้ในตารางนี้ หากใช้ reverse proxy โดยยังไม่ตั้ง trusted proxy ผู้ใช้ที่มาจาก proxy เดียวกันจะใช้โควตา IP ร่วมกัน ดูการตั้งค่าใน [คู่มือนำระบบขึ้นใช้งาน](docs/deployment.md)

### สำรองข้อมูลอัตโนมัติ

บริการ `backup` สำรอง PostgreSQL พร้อมไฟล์ภาพและฟอนต์ลง `backups/` ทุกวันเวลา **03:00 น. เขตเวลา Asia/Bangkok** ขณะ Docker ทำงาน หากปิดเครื่องข้ามรอบและชุดล่าสุดเก่าครบ 24 ชั่วโมง จะสำรองชดเชยเมื่อเปิดบริการ เก็บชุดสำรองอัตโนมัติที่ตรวจความครบถ้วนผ่านล่าสุด **14 ชุด** โดยไม่ลบไฟล์สำรองที่ทำเอง

```powershell
.\scripts\backup.ps1 status
.\scripts\backup.ps1 run
.\scripts\backup.ps1 verify
```

`verify` กู้ฐานข้อมูลและไฟล์จากชุดล่าสุดจริงในคอนเทนเนอร์ PostgreSQL 18 ชั่วคราว ใช้ชื่อ `pkru_certificate` และ schema `public` แล้วลบคอนเทนเนอร์หลังตรวจ ไม่กู้ทับข้อมูลที่กำลังใช้ ผลตรวจอยู่ใน `restore-verification.json` ของชุดสำรองนั้น การสำรองบนเครื่องเดียวกันยังต้องคัดลอกชุดสำรองไปไว้อีกเครื่องหรือไดรฟ์เพื่อรองรับกรณีดิสก์เสีย ดูรายละเอียดและวิธีเลือกชุดใน [คู่มือสำรองข้อมูล](docs/deployment.md#สำรองข้อมูลอัตโนมัติ)

### เปิด–ปิดและดูสถานะ

บน Windows ที่ตั้งค่าแล้ว ใช้คำสั่งสั้นจากโฟลเดอร์โปรเจกต์ได้:

```powershell
.\scripts\docker.ps1 start
.\scripts\docker.ps1 stop
.\scripts\docker.ps1 status
.\scripts\docker.ps1 logs
```

`restart` เริ่มเฉพาะเว็บใหม่ ส่วน `stop` หยุดทั้งชุดโดยเก็บข้อมูลไว้ กด Ctrl+C เพื่อออกจากการดู log
ถ้า PowerShell จำกัดการรันสคริปต์ ใช้ `powershell -ExecutionPolicy Bypass -File scripts/docker.ps1 status` (เปลี่ยนคำท้ายเป็นคำสั่งที่ต้องการ)

ใน Docker Desktop ให้เปิดหน้า Containers แล้วเลือกกลุ่ม **pkru-certificate** เว็บอยู่ที่ **http://localhost:8000**
ใช้ปุ่ม Start/Stop ของกลุ่มเพื่อเปิด–ปิด ส่วน `migrate` ที่แสดง `Exited (0)` เป็นงานเตรียมฐานข้อมูลที่จบสำเร็จตามปกติ

หลังย้ายมา Docker ให้เปิดใช้งานผ่านชุดนี้ ข้อมูลใหม่เก็บใน Docker volumes; ฐานข้อมูล Windows ที่เก็บสำรองไว้ไม่ได้ซิงก์การเปลี่ยนแปลงกลับไปด้วย

```text
# หยุดระบบ / เปิดอีกครั้ง
docker compose --env-file .env.docker stop
docker compose --env-file .env.docker start

# ดู log
docker compose --env-file .env.docker logs --tail 100 web migrate

# อัปเกรดหลังแก้โค้ด (สำรองข้อมูลและหยุด web/scheduler/backup ก่อน)
docker compose --env-file .env.docker build
docker compose --env-file .env.docker run --rm --no-deps migrate
docker compose --env-file .env.docker up -d
```

ข้อมูลอยู่ใน volumes `postgres_data` และ `certificate_storage` ของ project `pkru-certificate` การเปลี่ยนชื่อ project จะใช้ volumes คนละชุด **ห้ามใช้ `down -v` หากต้องเก็บข้อมูล** เพราะจะลบ volumes ด้วย

`/healthz` ใช้ตรวจว่าเว็บเชื่อมต่อฐานข้อมูลได้ ฟอนต์ภาษาไทยและใบอนุญาตอยู่ใน [static/fonts](static/fonts/LICENSES.md)

## เปลี่ยน URL และอัปเดต QR ในใบประกาศเดิม

ระบบยังเก็บใบประกาศเป็น PNG เมื่อเว็บเริ่มทำงานจะตรวจ `PUBLIC_BASE_URL` และทยอยอัปเดตเฉพาะ QR ที่ใช้ URL อื่น โดยคงรหัสตรวจสอบ ข้อความ ลายเซ็น และพิกเซลนอกกรอบ QR เดิมไว้ การค้นหาและดาวน์โหลดอ่านไฟล์ที่เก็บไว้ตามเดิม งานอัปเดตทำเบื้องหลังทีละใบและใช้ PostgreSQL advisory lock ป้องกัน worker/คำสั่งซ้ำกัน โดยไม่เพิ่มตารางหรือฐานข้อมูล

ใบใหม่เก็บตำแหน่ง QR ไว้ใน metadata ของ PNG ส่วนใบเก่าตรวจตำแหน่งจาก QR ในภาพจริง หากอ่านไม่ได้หรือยืนยันขอบเขตไม่ได้ จะเก็บภาพเดิมและรายงานหมายเลขรายการพร้อมเหตุผล การเขียนไฟล์ใช้ไฟล์ชั่วคราว ตรวจว่า QR ใหม่อ่านได้และส่วนอื่นไม่เปลี่ยน แล้วจึงแทนที่ไฟล์เดิม

หลังแก้ `PUBLIC_BASE_URL` ใน `.env.docker` ให้ใช้ URL แบบ `https://ชื่อโดเมน` หรือมี path ได้ โดยไม่มี query, fragment หรือชื่อผู้ใช้/รหัสผ่าน สามารถตรวจล่วงหน้าโดยไม่แก้ไฟล์:

```text
docker compose --env-file .env.docker run --rm --no-deps web python -m scripts.update_certificate_qr --dry-run
```

เมื่อพร้อมให้สร้างเฉพาะ web container ใหม่เพื่อรับค่าที่เปลี่ยน (คำสั่ง restart อย่างเดียวไม่รับค่า environment ใหม่):

```text
docker compose --env-file .env.docker up -d --no-deps --force-recreate web
```

หากตั้ง `QR_UPDATE_ON_STARTUP=false` หรือมีรายการที่ต้องลองใหม่ สามารถสั่งอัปเดตด้วย:

```text
docker compose --env-file .env.docker run --rm --no-deps web python -m scripts.update_certificate_qr --apply
```

คำสั่งรายงานยอด `updated` (เปลี่ยน QR), `indexed` (เพิ่ม metadata ให้ภาพเก่าที่ URL ตรงอยู่แล้ว), `unchanged` และ `failed` หากงานอื่นกำลังทำอยู่จะแสดง `busy` ให้รอแล้วลองใหม่; งานที่อัปเดตผ่านแล้วจะไม่แก้ไฟล์ซ้ำเมื่อ URL ยังเดิม ไฟล์ที่มีปัญหาจะอยู่ใน `failures` โดยไม่มีข้อมูลผู้รับ

ไฟล์ที่ผู้รับดาวน์โหลดหรือพิมพ์ออกไปก่อนเปลี่ยน URL จะไม่เปลี่ยนตาม ต้องดาวน์โหลดใหม่หรือให้โดเมนเก่าส่งต่อไปโดเมนใหม่ การใช้โดเมนถาวรช่วยให้ใบที่แจกไปยังใช้ลิงก์เดิมได้

## การดูแลโครงสร้าง SQL

`sql/schema.sql` สร้างจาก `models.py` ของเวอร์ชันนี้ หากผู้พัฒนาเปลี่ยนโครงสร้าง ต้องเพิ่ม Alembic migration สำหรับฐานข้อมูลเดิม แล้วอัปเดต SQL หลักด้วย:

```text
python scripts/export_schema.py
```

คำสั่งนี้เขียนไฟล์ SQL โดยไม่เชื่อมต่อหรือสร้างฐานข้อมูลเพิ่มเติม


### รูปแบบเก็บเกียรติบัตรและขนาดไฟล์

เกียรติบัตรใหม่เก็บเป็น **WebP lossless** โดยไม่ลดความละเอียดหรือจำนวนสี
ค่าเริ่มต้นคือ `CERTIFICATE_STORAGE_FORMAT=webp` และ `WEBP_METHOD=4`
โหมด lossless ใช้ quality=100 เพื่อเลือกระดับความพยายามบีบอัด ไม่ใช่ลดคุณภาพภาพ
หน้าเว็บแสดง WebP ได้โดยตรง ส่วนปุ่มดาวน์โหลดและไฟล์ใน ZIP ยังคงเป็น PNG
การดาวน์โหลด JPG/PDF และการเพิกถอนทำงานเหมือนเดิม

- PNG เดิมยังอ่านและอัปเดต QR ได้ ไม่มีการแปลงทับไฟล์เก่าอัตโนมัติ
- Metadata QR เก็บใน XMP ของ WebP และคืนเป็น PNG text เมื่อดาวน์โหลด
- รับภาพได้ไม่เกิน 16,383 พิกเซลต่อด้าน; ภาพใหญ่เกินจะแจ้งข้อผิดพลาด ไม่เก็บ PNG แทน
- ตั้ง `CERTIFICATE_STORAGE_FORMAT=png` ได้หากต้องการให้ใบใหม่กลับไปเก็บ PNG
- `PNG_COMPRESS_LEVEL=9` ควบคุม PNG ที่ดาวน์โหลด/เก็บ และการอัปเดต QR ของ PNG
- ถ้าเคยกำหนด environment ไว้ ค่านั้นมีผลเหนือค่าเริ่มต้น
- สำรองทั้งฐานข้อมูลและโฟลเดอร์ storage เหมือนเดิม ไม่ต้องเพิ่ม schema หรือตาราง

ทดลองซ้ำจากโฟลเดอร์โครงการ:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_webp.py --runs 3
```

หลังอัปเดตโค้ด ให้ build และ recreate เว็บโดยใช้ข้อมูลเดิม:

```powershell
docker compose --env-file .env.docker up -d --build web
```

คำสั่งนี้ไม่ลบ volume ฐานข้อมูลหรือ storage; ไม่ต้องใช้ `down -v`
ค่า `.env.docker` สำหรับรูปแบบใหม่คือ `CERTIFICATE_STORAGE_FORMAT=webp` และ `WEBP_METHOD=4`
รายละเอียดผลทดลองและข้อจำกัดอยู่ใน [WebP storage](docs/webp-storage.md)


Koyeb + Supabase deployment: [step-by-step guide](docs/koyeb-supabase.md).
