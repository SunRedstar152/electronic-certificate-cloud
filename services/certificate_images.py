"""Lossless storage codec and format-independent certificate metadata.

PNG text is carried in a namespaced XMP attribute when stored as WebP.
Downloads restore it to PNG text; no quantization or resizing is performed.
"""
from __future__ import annotations
import json
from tempfile import SpooledTemporaryFile
from xml.etree import ElementTree as ET
from PIL import Image, PngImagePlugin

WEBP_MAX_DIMENSION = 16383
_TEXT_ATTRIBUTE = "{urn:pkru:certificate:metadata:1}png_text"
_DPI_ATTRIBUTE = "{urn:pkru:certificate:metadata:1}png_dpi"
_DESCRIPTION = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description"

def is_webp(file) -> bool:
    position = file.tell()
    try:
        header = file.read(12)
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    finally:
        file.seek(position)

def _description(image):
    raw = image.info.get("xmp")
    if not raw:
        return None
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > 1024 * 1024 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Invalid certificate metadata")
    try:
        descriptions = [item for item in ET.fromstring(raw).iter(_DESCRIPTION) if _TEXT_ATTRIBUTE in item.attrib]
    except ET.ParseError as exc:
        raise ValueError("Invalid certificate metadata") from exc
    if len(descriptions) > 1:
        raise ValueError("Ambiguous certificate metadata")
    return descriptions[0] if descriptions else None

def image_text(image) -> dict[str,str]:
    if image.format != "WEBP":
        return dict(getattr(image, "text", {}))
    description = _description(image)
    if description is None:
        return {}
    value = json.loads(description.attrib[_TEXT_ATTRIBUTE])
    if not isinstance(value, dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in value.items()):
        raise ValueError("Invalid certificate text metadata")
    return value

def image_options(image) -> dict:
    options = {key: image.info[key] for key in ("dpi", "icc_profile", "exif") if key in image.info}
    if image.format == "WEBP":
        description = _description(image)
        if description is not None and _DPI_ATTRIBUTE in description.attrib:
            dpi = json.loads(description.attrib[_DPI_ATTRIBUTE])
            if not isinstance(dpi,list) or len(dpi)!=2 or not all(isinstance(v,(int,float)) and 0 < v < 100000 for v in dpi):
                raise ValueError("Invalid DPI metadata")
            options["dpi"] = tuple(dpi)
    return options

def save_image(image, output, *, file_format: str, text: dict[str,str],
               compress_level: int=9, webp_method: int=4, options: dict|None=None):
    options = dict(options or {})
    if file_format.upper() == "PNG":
        info = PngImagePlugin.PngInfo()
        for key,value in text.items():
            info.add_text(key,value)
        image.save(output,format="PNG",compress_level=compress_level,pnginfo=info,**options)
    elif file_format.upper() == "WEBP":
        xmp = ET.Element("{adobe:ns:meta/}xmpmeta")
        rdf = ET.SubElement(xmp,"{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF")
        description = ET.SubElement(rdf,_DESCRIPTION)
        description.set(_TEXT_ATTRIBUTE,json.dumps(text,ensure_ascii=True,separators=(",",":")))
        dpi = options.pop("dpi",None)
        if dpi is not None:
            description.set(_DPI_ATTRIBUTE,json.dumps(dpi))
        image.save(output,format="WEBP",lossless=True,exact=True,quality=100,method=webp_method,
                   xmp=ET.tostring(xmp,encoding="utf-8"),**options)
    else:
        raise ValueError("Unsupported certificate storage format")

def png_export(file, *, compress_level: int=9):
    """Return a seekable PNG stream; large outputs spill to temporary disk."""
    output = SpooledTemporaryFile(max_size=8*1024*1024, mode="w+b")
    try:
        with Image.open(file) as image:
            image.load()
            save_image(image,output,file_format="PNG",text=image_text(image),
                       options=image_options(image),compress_level=compress_level)
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise
