# คู่มือดูแลและนำระบบขึ้นใช้งาน

เริ่มตาม [README](../README.md) ก่อน คู่มือนี้ใช้ `compose.yaml` และ `.env.docker` คำสั่งทั้งหมดรันจากโฟลเดอร์โปรเจกต์

ฐานข้อมูลหลักคือ PostgreSQL 18 ชื่อ `pkru_certificate` ใช้ schema `public` เท่านั้น ไฟล์สร้างโครงสร้างหลักมีไฟล์เดียว: **[`sql/schema.sql`](../sql/schema.sql)** สำหรับฐานข้อมูลใหม่ที่ว่าง service `migrate` จะอ่านไฟล์นี้แล้วบันทึกเวอร์ชัน ส่วนฐานข้อมูลที่มีอยู่แล้วใช้ประวัติใน `alembic/` เพื่ออัปเกรด ทั้งหมดทำงานกับฐานข้อมูลเดียวกัน

## องค์ประกอบและข้อมูลถาวร

| Service | หน้าที่ | พื้นที่เก็บข้อมูล |
| --- | --- | --- |
| `db` | PostgreSQL 18 | `postgres_data` ที่ `/var/lib/postgresql` |
| `migrate` | สร้างจาก `sql/schema.sql` เมื่อว่าง แล้วตรวจ/อัปเกรดด้วย Alembic | DB และ storage เดียวกับเว็บ |
| `web` | FastAPI/Uvicorn พอร์ต 8000 ในคอนเทนเนอร์ | `certificate_storage` ที่ `/app/storage` |
| `scheduler` | ล้าง audit log เก่า เปิดด้วย profile `maintenance` | DB เดียวกับเว็บ |
| `backup` | สำรอง DB และ storage ทุกวัน 03:00 น. | โฟลเดอร์ `backups/` บน host; อ่าน storage อย่างเดียว |

Storage มี `uploads/templates`, `uploads/fonts` และ `generated/certificates` ต้องสำรองฐานข้อมูลและ storage ให้เป็นชุดเดียวกัน Image ไม่มีข้อมูลผู้ใช้ ไฟล์ env หรือไฟล์สำรอง เว็บรันด้วยผู้ใช้ `app` (UID/GID 10001)

Compose รอฐานข้อมูล healthy และ migration สำเร็จก่อนเริ่มเว็บ ตาม [ลำดับเริ่ม service ของ Docker Compose](https://docs.docker.com/compose/how-tos/startup-order/) เปิด scheduler เพียงหนึ่งตัว เพราะ job store ไม่เลือกผู้นำให้

## ใช้งานผ่าน HTTPS

ตั้ง `PUBLIC_BASE_URL=https://ชื่อโดเมนจริง` ให้ reverse proxy บน host ส่งไป `127.0.0.1:8000` ค่าเริ่มต้น Compose เปิดเว็บเฉพาะ loopback และไม่เผยพอร์ต PostgreSQL

ตัวอย่าง Caddyfile เมื่อ Caddy อยู่บน host เดียวกัน:

```caddyfile
certificates.example.edu {
    reverse_proxy 127.0.0.1:8000
}
```

เปลี่ยนโดเมน ตั้ง DNS และเปิดพอร์ต 80/443 ให้ proxy ตามระบบจริง เว็บใช้ `PUBLIC_BASE_URL` สร้าง QR Code โดยตรง ค่าเริ่มต้น Uvicorn ใน image ไม่เชื่อ forwarded headers หากต้องใช้ IP/โปรโตคอลจาก proxy ให้ตั้ง `--proxy-headers` และ `--forwarded-allow-ips` เฉพาะ IP proxy ที่เชื่อถือได้ใน Compose override

หาก proxy อยู่ในคอนเทนเนอร์อื่น ต้องต่อ Docker network เดียวกันและส่งไป `web:8000`

ระบบล็อกอินใช้ IP จาก `request.client` ของ ASGI เท่านั้น ไม่อ่าน `X-Forwarded-For` ที่ผู้ใช้ส่งมาเอง เมื่อใช้ค่าเริ่มต้น `--no-proxy-headers` ผู้ใช้ผ่าน proxy เดียวกันจะใช้โควตา IP ร่วมกัน (รหัสผิดรวม 30 ครั้งต่อ 15 นาที) หากต้องการแยกตาม IP ผู้ใช้ ให้ตั้ง trusted proxy ตามย่อหน้าข้างต้นและให้ proxy เขียนทับ forwarded headers ของคำขอ ปรับค่าได้ด้วย `LOGIN_PAIR_MAX_FAILURES`, `LOGIN_IP_MAX_FAILURES` และ `LOGIN_WINDOW_SECONDS` ใน `.env.docker`

## สำรองข้อมูลอัตโนมัติ

`docker compose --env-file .env.docker up -d --build` เปิด service `backup` ด้วย ค่าเริ่มต้นสำรองทุกวัน **03:00 น. Asia/Bangkok** เมื่อเปิดเครื่อง/Docker ใหม่จะสำรองทันทีหากยังไม่มีชุดสำเร็จ ชุดล่าสุดเกิน 24 ชั่วโมง หรือเลยเวลารอบของวันนี้แล้ว หากล้มเหลวจะลองใหม่ทุก 15 นาที งานทำได้เฉพาะขณะที่เครื่องและ Docker ทำงาน

| ค่าใน `.env.docker` | ค่าเริ่มต้น | หน้าที่ |
| --- | --- | --- |
| `BACKUP_DIRECTORY` | `./backups` | โฟลเดอร์ปลายทางบน host |
| `BACKUP_TIMEZONE` | `Asia/Bangkok` | เขตเวลาของตารางสำรอง |
| `BACKUP_DAILY_TIME` | `03:00` | เวลาสำรองแบบ 24 ชั่วโมง |
| `BACKUP_RETAIN_COUNT` | `14` | จำนวนชุดสมบูรณ์ที่เก็บ อย่างน้อย 1 |
| `BACKUP_RETRY_SECONDS` | `900` | ระยะลองใหม่เมื่อล้มเหลว อย่างน้อย 30 วินาที |

แต่ละชุดชื่อ `auto-YYYYMMDDTHHMMSSZ-xxxxxxxx` ใช้เวลา UTC ในชื่อ ประกอบด้วย `database.dump`, `storage.tar.gz` และ `manifest.json` ซึ่งบันทึก SHA-256 จำนวนแถวแต่ละตาราง รายการอ้างอิงไฟล์ และ checksum ของไฟล์ทุกชิ้น ชุดจะปรากฏชื่อสมบูรณ์หลังตรวจ archive และไฟล์อ้างอิงผ่านเท่านั้น ระหว่างทำงานใช้ชื่อซ่อน `.partial`

ตัวสำรองใช้ PostgreSQL 18 client และ SHARE lock ทุกตารางใน `public` ระหว่างเก็บ [snapshot ฐานข้อมูล](https://www.postgresql.org/docs/18/app-pgdump.html) และ storage จึงไม่ต้องหยุดเว็บ แต่คำขอที่เขียนข้อมูลอาจรอจนเก็บเสร็จ ใช้ advisory lock กันสำรองซ้อนกัน หากรอ table lock เกิน 30 วินาทีจะล้มเหลวแล้วลองใหม่ ต้องเก็บไฟล์ที่เผยแพร่แล้วแบบชื่อเฉพาะดังที่แอปทำอยู่ และไม่ย้าย/ลบไฟล์ด้วยมือหรือรัน migration ระหว่างสำรอง

ตัวสำรองอ่าน volume storage อย่างเดียวและไม่มี Docker socket ในคอนเทนเนอร์ จะลบเฉพาะชุดที่โปรแกรมสร้างและตรวจความครบถ้วนผ่านซึ่งเกินจำนวนที่กำหนด ไม่ลบชุด manual ชุดไม่สมบูรณ์ หรือชุดเสียหาย การตรวจความครบถ้วนนี้ยังไม่ใช่การทดลองกู้คืนจริง ให้รันคำสั่ง `verify` ด้านล่างหลังติดตั้ง หลังอัปเกรด และเป็นระยะ

```powershell
.\scripts\backup.ps1 run       # สำรองทันที; รอจนเสร็จ
.\scripts\backup.ps1 status    # ดูชุดล่าสุด ผลสำรอง และผลทดลองกู้คืน
.\scripts\backup.ps1 verify    # ทดลองกู้คืนชุดล่าสุดจริง
# ระบุชุดเก่าได้โดยส่งชื่อโฟลเดอร์เป็น argument ที่สอง:
# .\scripts\backup.ps1 verify auto-20260909T030000Z-12345678
```

คำสั่งตรงสำหรับการสำรองและดูสถานะ:

```text
docker compose --env-file .env.docker run --rm --no-deps backup run-once
docker compose --env-file .env.docker exec -T backup python3 /backup-tools/backup.py status
docker compose --env-file .env.docker logs --tail 100 backup
```

`status` และ Docker healthcheck จะไม่ผ่านหากยังไม่เคยสำรองสำเร็จ ชุดล่าสุดเกิน 26 ชั่วโมง หรือรอบล่าสุดล้มเหลว ตรวจโฟลเดอร์ปลายทาง พื้นที่ดิสก์ และ log เมื่อไม่ผ่าน จำกัดสิทธิ์เข้าถึง `backups/` เพราะมีข้อมูลจริง และคัดลอกชุดสมบูรณ์ไปเก็บนอกเครื่องเพื่อรองรับดิสก์เสียหรือเครื่องสูญหาย

`backup.ps1 verify` สร้างคอนเทนเนอร์ PostgreSQL 18 ชั่วคราวที่ชื่อขึ้นต้น `pkru-restore-check-` ใช้ฐานข้อมูล `pkru_certificate` และ schema `public` โดยไม่เผยพอร์ตและไม่ต่อ network ของระบบจริง ข้อมูล PostgreSQL อยู่ใน tmpfs แล้วกู้คืน dump จริง เปรียบเทียบทุกตาราง/รายการอ้างอิงไฟล์ แตก archive ในพื้นที่ชั่วคราวและเทียบ checksum ทุกไฟล์ เมื่อผ่านจะเขียน `restore-verification.json` ลงในชุดสำรอง จากนั้นลบคอนเทนเนอร์และ anonymous volume ใน `finally` ทั้งกรณีผ่านและล้มเหลว จึงไม่มีฐานข้อมูลทดสอบค้างบน PostgreSQL หลัก หาก Docker ถูกปิดหรือกระบวนการถูกฆ่าระหว่างตรวจ ให้ตรวจคอนเทนเนอร์ที่มี label `pkru.restore-check` หลังเปิดเครื่องอีกครั้ง

สำรอง `.env.docker` ในที่ปลอดภัยแยกจาก Git และเก็บซอร์ส/image รุ่นเดียวกับชุดข้อมูลสำหรับกู้คืน built-in template และฟอนต์ที่ติดมากับแอป การใช้ `down` เก็บ volumes แต่ **`down -v` ลบข้อมูล** ห้ามใช้กับระบบจริงในขั้นตอนอัปเกรด

## กู้คืนบนระบบใหม่

ใช้กับ **ฐานข้อมูลและ volume ปลายทางใหม่ที่ว่าง** ไม่ใช่ทับระบบที่ใช้งาน:

1. ใช้ Compose project แยกหรือเครื่องใหม่ ตั้ง `.env.docker` แล้ว `docker compose --env-file .env.docker up -d db` รอให้ healthy
2. เลือกชุดที่ `verify` ผ่าน คัดลอก `backups/<ชื่อชุด>/database.dump` ไป `db:/tmp/restore.dump` ด้วย `docker compose ... cp` แล้วรัน:

   ```text
   docker compose --env-file .env.docker exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges --exit-on-error /tmp/restore.dump'
   ```

3. คัดลอก `storage.tar.gz` จากชุดเดียวกันเป็น `backups/restore-storage.tar.gz` แล้วคืน storage จากชุดสำรองที่เชื่อถือได้:

   ```powershell
   docker compose --env-file .env.docker run --rm --no-deps --volume "${PWD}/backups:/backup:ro" web python -c "import tarfile; t=tarfile.open('/backup/restore-storage.tar.gz'); t.extractall('/app',filter='data'); t.close()"
   ```

4. รัน `docker compose --env-file .env.docker up -d --build` เพื่ออัปเกรด schema และเปิดเว็บ
5. ตรวจ health, login, แม่แบบ, ฟอนต์ที่อัปโหลด และค้นหา/ดาวน์โหลดเกียรติบัตรเก่าจากชุดสำรอง

ถ้า restore หรือ migration ไม่สำเร็จ ให้หยุดตรวจ log ก่อนเปิดให้ใช้งาน

## ย้ายข้อมูลจาก Windows เดิม

Docker ไม่อ่าน DB และไฟล์บนเครื่องเดิมโดยอัตโนมัติ ต้องสำรอง PostgreSQL และโฟลเดอร์ `TEMPLATE_DIR`, `FONT_DIR`, `CERTIFICATE_DIR` เดิมก่อน แล้วทดสอบย้ายลงระบบแยกตามขั้นตอนกู้คืน

ตรวจพาธที่บันทึกในฐานข้อมูล:

| ตาราง/คอลัมน์ | พาธปลายทางใน Docker |
| --- | --- |
| `templates.background_image_path` | `/app/storage/uploads/templates/...` |
| `font_assets.file_path` | `/app/storage/uploads/fonts/...` |
| `certificates.image_path` | `/app/storage/generated/certificates/...` |

พาธสัมพัทธ์ `storage/...` ใช้จาก `/app` ได้ แต่พาธ Windows เช่น `D:/...` หรือพาธเก่าใน `static/` ต้องแปลง prefix ให้ตรงตำแหน่งใหม่ คงชื่อไฟล์และโฟลเดอร์หน่วยงานไว้ ตรวจค่าจริงและทำ transaction กับสำเนา DB ก่อนแก้ระบบจริง ห้ามแทนค่าพาธทุกแถวโดยไม่ตรวจรายการ

ไม่มีการย้ายพาธอัตโนมัติ เพราะตำแหน่งเดิมอาจต่างกันในแต่ละเครื่อง

## อัปเกรด

1. รัน `.\scripts\backup.ps1 run` และ `.\scripts\backup.ps1 verify` ให้ผ่าน แล้วหยุด `web`/`scheduler`/`backup` เพื่อไม่ให้รอบสำรองชน migration
2. อัปเดตซอร์สโดยเก็บ `.env.docker` เดิม ตรวจค่าใหม่จาก example
3. รันตามลำดับ โดยต้องรอ migration จบด้วยรหัส `0` ก่อนสั่ง `up`:

   ```text
   docker compose --env-file .env.docker build
   docker compose --env-file .env.docker run --rm --no-deps migrate
   docker compose --env-file .env.docker up -d
   ```

4. เปิด profile `maintenance` เพิ่มถ้าใช้งาน scheduler แล้วตรวจ health, login และเกียรติบัตรเดิม

ถ้า migration รายงานชื่อหน่วยงานซ้ำ ให้ตรวจเจ้าของข้อมูลและแก้ก่อนรันใหม่ อย่าเปลี่ยนชื่อหรือรวมอัตโนมัติ การย้อนเวอร์ชันต้องตรวจความเข้ากันได้ของ schema; หากต้องกู้ข้อมูลให้คืน DB และ storage คู่กัน

## คำสั่งเสริม

สำหรับคำสั่ง Python ในคอนเทนเนอร์ที่รันอยู่ ใช้ entrypoint เตรียม `DATABASE_URL` ก่อน เช่น:

```text
docker compose --env-file .env.docker exec web python scripts/container_entrypoint.py alembic current
```

## ตรวจปัญหา

| อาการ | ตรวจสอบ |
| --- | --- |
| ติดต่อ Docker engine ไม่ได้ | เปิด Docker Desktop และตรวจ Linux engine |
| Compose แจ้ง secret ไม่ได้ตั้ง | ใส่ค่าจริงใน `.env.docker` และใช้ `--env-file .env.docker` |
| `migrate` error | ดู `docker compose ... logs migrate db` และตรวจ schema/ข้อมูลเดิม |
| เปลี่ยนรหัส DB ใน env แล้วเข้าไม่ได้ | ค่าเริ่มต้น PostgreSQL ใช้ตอนสร้าง volume ครั้งแรก การแก้ env ไม่เปลี่ยนรหัส role เดิม |
| เว็บไม่ healthy | ดู log `web`/`db`; health ตรวจการเชื่อมต่อ DB |
| พอร์ตถูกใช้ | เปลี่ยน `APP_PORT` และ `PUBLIC_BASE_URL` ให้ตรง |
| ภาพหรือฟอนต์หายหลังย้าย | ตรวจ storage, พาธใน DB และสิทธิ์ UID 10001 |
| `backup` ไม่ healthy | รัน `.\scripts\backup.ps1 status` และดู log; ตรวจพื้นที่ปลายทาง ไฟล์อ้างอิงที่หาย และ DB lock |
| QR เปิดไม่ได้จากมือถือ | URL ต้องเป็นโดเมน/IP ที่มือถือเข้าถึง ไม่ใช่ localhost |

รักษา [ฟอนต์และใบอนุญาต](../static/fonts/LICENSES.md) ไว้ Docker ติดตั้ง RAQM/HarfBuzz/FriBiDi และตรวจ Pillow ระหว่าง build ตาม [เอกสาร Pillow](https://pillow.readthedocs.io/en/stable/installation/basic-installation.html)
