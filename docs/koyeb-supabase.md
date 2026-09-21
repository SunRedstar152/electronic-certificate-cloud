# ติดตั้งบน Koyeb + Supabase

คู่มือนี้ใช้ฐานข้อมูล Supabase และ bucket ส่วนตัว ส่วน Koyeb รัน FastAPI จาก Dockerfile ของโครงการ ไม่ใช้ดิสก์ของ Koyeb เป็นที่เก็บไฟล์ถาวร

## 1. เตรียม Supabase

- สร้าง bucket ชื่อ `certificate-files` และปิด Public
- ใช้ `sql/schema.sql` สำหรับฐานข้อมูลใหม่เท่านั้น ไม่รันล้างฐานข้อมูลที่มีข้อมูลแล้ว
- ตรวจตารางใน schema `public` และ `public.alembic_version` รุ่น `b6c7d8e9f0a1`
- ปิด Data API หากไม่ได้ใช้: เว็บนี้เชื่อม PostgreSQL โดยตรงและตรวจสิทธิ์ผ่าน FastAPI
- เก็บ Session pooler URI (พอร์ต 5432), Project URL และ Secret key ไว้ส่วนตัว
- บัญชีผู้ใช้ในระบบนี้แยกจาก Supabase Auth

## 2. เตรียมโค้ดบน GitHub

ใช้รุ่นที่มี `services/object_storage.py` และ `scripts/start_cloud.py` ตรวจให้ไฟล์เหล่านี้อยู่บน branch ที่เลือก deploy จริง อย่าเลือก main เก่าที่ยังไม่มีการเชื่อม Storage

อย่า commit `.env`, `.env.docker`, รหัสผ่าน หรือไฟล์สำรองข้อมูล

## 3. สร้างบริการ Koyeb

เลือก GitHub repository ของโครงการ แล้วเลือก branch ที่เตรียมไว้ ใช้ Dockerfile ที่รากโครงการ ไม่ใช้ buildpack ตั้ง run command เป็น:

```text
python scripts/start_cloud.py
```

ใช้หนึ่ง instance และหนึ่ง web worker ตั้งพอร์ต HTTP เป็น `8000` และ health check เป็น `/healthz` เลือก Free เฉพาะเมื่อหน้า console แสดงให้เลือก ตรวจแผนและเงื่อนไขบัญชีก่อนยืนยัน ไม่ต้องสร้าง PostgreSQL เพิ่มที่ Koyeb

## 4. Environment variables

| ชื่อ | ค่า |
| --- | --- |
| DATABASE_URL | Session pooler URI ตามตัวอย่างด้านล่าง เก็บเป็น Secret |
| SECRET_KEY | ค่าสุ่มอย่างน้อย 32 ตัวอักษร เก็บเป็น Secret และใช้ค่าเดิมเมื่อ redeploy |
| STORAGE_BACKEND | `supabase` |
| SUPABASE_URL | `https://PROJECT_REF.supabase.co` ของโครงการจริง |
| SUPABASE_SECRET_KEY | Secret key ฝั่ง server (`sb_secret_...`) เก็บเป็น Secret |
| SUPABASE_STORAGE_BUCKET | `certificate-files` |
| STORAGE_CACHE_MB | `256` |
| STORAGE_TIMEOUT_SECONDS | `30` |
| CERTIFICATE_STORAGE_FORMAT | `webp` |
| WEBP_METHOD | `4` |
| REQUIRE_RAQM | `true` |
| QR_UPDATE_ON_STARTUP | `false` |
| ENABLE_SCHEDULER | `false` |
| PORT | `8000` |
| PUBLIC_BASE_URL | URL HTTPS จริงของบริการ Koyeb |
| FIRST_ADMIN_USERNAME | ชื่อแอดมินคนแรก |
| FIRST_ADMIN_PASSWORD | รหัสผ่านแอดมินคนแรก เก็บเป็น Secret |

รูปแบบ URI (ใช้ host และ project ref ที่ Supabase แสดง ห้ามคัดลอก placeholder ไปใช้จริง):

```text
postgresql+psycopg://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
```

เปลี่ยนส่วนต้น `postgresql://` เป็น `postgresql+psycopg://` รหัสผ่านที่มีอักขระพิเศษต้อง URL-encode เช่น `@` เป็น `%40` และ `#` เป็น `%23` อย่าใช้เว็บภายนอกแปลงรหัสผ่าน อย่าใช้ Transaction pooler พอร์ต 6543 แทน Session pooler

สร้าง SECRET_KEY ในเครื่องด้วย:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

รหัสแอดมินต้องยาวอย่างน้อย 12 ตัวอักษร มีตัวพิมพ์ใหญ่ ตัวพิมพ์เล็ก ตัวเลข และสัญลักษณ์ ระบบสร้างเฉพาะเมื่อยังไม่มีแอดมิน ไม่เปลี่ยนรหัสของบัญชีเดิม เมื่อเข้าใช้งานสำเร็จ ให้ลบ FIRST_ADMIN_PASSWORD และ FIRST_ADMIN_USERNAME ออกจากค่าบริการแล้ว redeploy

หากยังไม่ได้ URL ของ Koyeb ให้ deploy เพื่อรับ URL ก่อน จากนั้นตั้ง PUBLIC_BASE_URL ให้ตรงและ redeploy อีกครั้ง **ก่อนออกเกียรติบัตรใบแรก** QR ที่พิมพ์ลงภาพแล้วไม่เปลี่ยนเองตาม URL ใหม่

## 5. ตรวจหลัง deploy

1. เปิด `/healthz` ต้องสำเร็จ และเปิดหน้าเว็บได้
2. เข้าหน้า `/login` ด้วยบัญชีแอดมินที่สร้างไว้
3. สร้างหน่วยงานและบัญชีผู้ใช้ตามขั้นตอนของระบบ
4. อัปโหลดแม่แบบ PNG/JPG แล้วออกเกียรติบัตรหนึ่งใบ ตรวจ QR และดาวน์โหลด PNG/PDF/ZIP
5. ตรวจ bucket: ไฟล์ใหม่อยู่ใต้ `templates/`, `certificates/` และ `fonts/` ตามประเภท
6. Redeploy Koyeb แล้วเปิดใบเดิมอีกครั้ง เพื่อยืนยันว่าอ่านไฟล์จาก Supabase ได้จริง
7. ทดสอบเพิกถอนใบ ใบที่เพิกถอนต้องไม่เปิดภาพผ่านหน้าสาธารณะได้

ทดสอบในเครื่องด้วย PostgreSQL 17.6 และ bucket จำลองช่วยตรวจตรรกะได้ แต่ยังต้องทดสอบขั้นตอนข้างต้นบน Supabase จริง จึงจะยืนยันการตั้งค่าและสิทธิ์ของบัญชีได้

## การเก็บข้อมูลและย้ายไป VPS

ฐานข้อมูลเก็บ reference เช่น `storage://certificates/ชื่อไฟล์.webp` ไฟล์จริงอยู่ใน bucket ส่วนตัว FastAPI ตรวจสิทธิ์ก่อนอ่านจาก Storage; Secret key ไม่ส่งให้ browser แม่แบบต้นฉบับอยู่ใน assets ของโค้ด

ดิสก์ Koyeb เป็นแคชชั่วคราว มีอายุความสด 30 วินาทีและจำกัดพื้นที่ตาม STORAGE_CACHE_MB เมื่อพื้นที่ไม่พอและไฟล์ยังถูกใช้งานล่าสุด ระบบอาจให้ลองใหม่ ไฟล์หนึ่งต้องไม่เกิน 50 MiB ใน adapter นี้ ข้อจำกัด bucket อาจต่ำกว่านั้นตามการตั้งค่า

การตั้งค่านี้เริ่มฐานข้อมูลบนคลาวด์แยกจาก Docker ในเครื่อง ไม่ได้ย้ายบัญชีหรือใบเดิมให้อัตโนมัติ ห้ามเปลี่ยน DATABASE_URL ของเครื่องจริงโดยไม่ได้เตรียมย้ายข้อมูล

ย้ายไป VPS ได้โดยสำรอง PostgreSQL และดาวน์โหลดไฟล์ **ทุก object** จาก bucket ให้ครบ รักษาชื่อและโฟลเดอร์ย่อยเดิม วาง `templates/` ที่ TEMPLATE_DIR, `certificates/` ที่ CERTIFICATE_DIR และ `fonts/` ที่ FONT_DIR แล้วใช้ STORAGE_BACKEND=local ตัวอ่านรองรับ reference เดิม เก็บ SECRET_KEY เดิมและตั้ง PUBLIC_BASE_URL ให้ถูกต้อง ตรวจใบเดิมก่อนเปิดรับงาน

ต้องสำรองทั้งฐานข้อมูลและไฟล์ใน bucket แยกกัน สคริปต์สำรอง Docker volume เดิมไม่ได้สำรองไฟล์บน Supabase ให้ ห้ามลบโครงการ Supabase ก่อนตรวจการย้ายครบ

## เอกสารผู้ให้บริการ

- https://supabase.com/docs/guides/database/connecting-to-postgres
- https://supabase.com/docs/guides/storage/serving/downloads
- https://supabase.com/docs/guides/getting-started/api-keys
- https://www.koyeb.com/docs/build-and-deploy/build-from-git
