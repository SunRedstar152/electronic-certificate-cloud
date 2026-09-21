import os, sys, json, time, statistics, io
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
os.environ['DATABASE_URL']='postgresql+psycopg://unused:unused@127.0.0.1:1/unused'
os.environ['SECRET_KEY']='synthetic-benchmark-not-a-real-secret-123456'
sys.path.insert(0,str(Path.cwd()))
from PIL import Image
from models import Certificate, Template
from services.builtin_templates import original_definition
from services.image_builder import render_certificate
from services.signatures import sample_signature
from services.certificate_qr import issuance_png_info, METADATA_KEY
from PIL.PngImagePlugin import PngInfo
import zxingcpp
spec=original_definition()
template=Template(organization_id=1,layout=spec['layout'],dimensions=spec['dimensions'],positions=spec['positions'],background_image_path='builtin:original')
cert=Certificate(organization_id=1,recipient_name='ตัวอย่างผู้รับเกียรติบัตร',topic='กิจกรรมทดสอบประสิทธิภาพ',issue_date=date(2026,9,20),verification_code='ABCD1234EFGH5678',custom_fields={})
signatures={e['id']:sample_signature() for e in spec['layout']['elements'] if e['kind']=='signature'}
results=[]
try:
 with TemporaryDirectory() as directory:
  path=render_certificate(template,cert,Path(directory)/'sample.png',signatures=signatures)
  with Image.open(path) as src:
   src.load()
   pixels=src.tobytes()
   meta=PngInfo()
   for k,v in src.text.items():meta.add_text(k,v)
   expected_qr=[x.text for x in zxingcpp.read_barcodes(src)]
   for name,opts in [('default',{}),('level9',{'compress_level':9}),('optimize',{'optimize':True})]:
    timings=[]
    for _ in range(5):
     buffer=io.BytesIO();started=time.perf_counter();src.save(buffer,format='PNG',pnginfo=meta,**opts);timings.append(time.perf_counter()-started)
    with Image.open(io.BytesIO(buffer.getvalue())) as check:
     assert check.size==src.size and check.mode==src.mode and check.tobytes()==pixels
     assert check.text==src.text
     assert [x.text for x in zxingcpp.read_barcodes(check)]==expected_qr
    results.append({'mode':name,'bytes':len(buffer.getvalue()),'median_encode_ms':round(statistics.median(timings)*1000,2),'pixels_metadata_qr_equal':True})
finally:
 for signature in signatures.values():signature.close()
print(json.dumps({'dimensions':spec['dimensions'],'runs_per_mode':5,'results':results},indent=2))