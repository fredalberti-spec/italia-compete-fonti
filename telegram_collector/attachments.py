"""Inert, bounded attachment validation/extraction. No renderer, shell or network."""
import io,posixpath,re,struct,zipfile,xml.etree.ElementTree as ET
from pathlib import PurePosixPath
MAX_BYTES=10*1024*1024
MAX_EXPANDED=60*1024*1024
MAX_XML=5*1024*1024
MAX_TEXT=200000
MIME={'pdf':'application/pdf','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','epub':'application/epub+zip','png':'image/png','jpg':'image/jpeg','webp':'image/webp','ogg':'audio/ogg','mp3':'audio/mpeg','wav':'audio/wav','flac':'audio/flac','m4a':'audio/mp4'}
ALIASES={'jpeg':'jpg','opus':'ogg'}
MIME_ALIASES={'audio/opus':'audio/ogg','audio/x-wav':'audio/wav','audio/x-flac':'audio/flac','audio/x-m4a':'audio/mp4'}
class RejectedAttachment(ValueError):pass

def reject():raise RejectedAttachment('AttachmentRejected')
def safe_path(n):
    if (not isinstance(n,str) or not n or n.startswith('/') or '\\' in n or ':' in n or '%' in n
        or any(ord(c)<32 for c in n) or any(p in ('.','..') for p in n.split('/'))):reject()
    return n

def hint(a,kind):
    if not isinstance(a,dict) or not isinstance(a.get('file_id'),str) or not a['file_id']:reject()
    size=a.get('file_size')
    if size is not None and (type(size) is not int or not 0<size<=MAX_BYTES):reject()
    name=a.get('file_name')
    ext=None
    if name is not None:
        safe_path(name)
        if '/' in name or len(name)>255:reject()
        ext=ALIASES.get(PurePosixPath(name).suffix[1:].lower(),PurePosixPath(name).suffix[1:].lower())
        if ext not in MIME:reject()
    if kind=='document' and ext is None:reject()
    mime=a.get('mime_type')
    if mime is not None and mime!='application/octet-stream':
        if mime not in set(MIME.values())|{'audio/opus','audio/x-wav','audio/x-flac','audio/x-m4a'}:reject()
        if ext and MIME_ALIASES.get(mime,mime)!=MIME[ext]:reject()
    if kind in ('voice','audio'):
        duration=a.get('duration')
        if duration is not None and (type(duration) is not int or not 0<=duration<=1800):reject()
    return ext,mime

def xml(body):
    if len(body)>MAX_XML:reject()
    try:
        if body.startswith((b'\xff\xfe',b'\xfe\xff')):s=body.decode('utf-16')
        elif body.startswith(b'<\x00'):s=body.decode('utf-16le')
        elif body.startswith(b'\x00<'):s=body.decode('utf-16be')
        else:s=body.decode('utf-8-sig')
        s=re.sub(r'<!DOCTYPE\s+html\s*>','',s,flags=re.IGNORECASE)
        if '<!DOCTYPE' in s.upper() or '<!ENTITY' in s.upper():reject()
        root=ET.fromstring(s)
        stack=[(root,1)];count=0
        while stack:
            e,d=stack.pop();count+=1
            if d>64 or count>100000:reject()
            stack.extend((c,d+1) for c in e)
        return root
    except (UnicodeError,ET.ParseError):reject()

def local(e):return e.tag.rsplit('}',1)[-1]
def visible(root):
    chunks=[];used=0
    def walk(e):
        nonlocal used
        if local(e).lower() in ('script','style','iframe','object','embed'):return
        if e.text:
            t=e.text[:MAX_TEXT-used];chunks.append(t);used+=len(t)
        for c in e:
            if used>=MAX_TEXT:break
            walk(c)
            if c.tail:
                t=c.tail[:MAX_TEXT-used];chunks.append(t);used+=len(t)
        if local(e).lower() in ('p','div','section','h1','h2','li'):chunks.append('\n')
    walk(root);return ''.join(chunks)[:MAX_TEXT]

def package(body,ext):
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            infos=z.infolist()
            if not infos or len(infos)>4000:reject()
            names=set();total=0
            for i in infos:
                safe_path(i.filename)
                if i.filename in names or i.flag_bits&1 or i.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):reject()
                names.add(i.filename);total+=i.file_size
                if (total>MAX_EXPANDED or i.file_size>MAX_EXPANDED or i.file_size/max(1,i.compress_size)>100
                    or (i.external_attr>>16)&0o170000==0o120000):reject()
                if PurePosixPath(i.filename).suffix.lower() in ('.exe','.dll','.bat','.cmd','.scr','.com','.ps1','.vbs'):reject()
            if total/max(1,len(body))>50:reject()
            # CRC/decompression verified with bounded streaming; never extract paths to disk.
            for i in infos:
                with z.open(i) as f:
                    read=0
                    while True:
                        part=f.read(min(65536,i.file_size-read+1))
                        if not part:break
                        read+=len(part)
                        if read>i.file_size:reject()
                if read!=i.file_size:reject()
            def read(n):
                safe_path(n)
                if n not in names or z.getinfo(n).file_size>MAX_XML:reject()
                return z.read(n)
            if ext=='docx':
                if any('vbaProject' in n or n.startswith('word/activeX/') for n in names):reject()
                ct=xml(read('[Content_Types].xml'))
                if local(ct)!='Types' or not any(e.attrib.get('ContentType')=='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml' for e in ct):reject()
                doc=xml(read('word/document.xml'))
                if local(doc)!='document':reject()
                for n in names:
                    if n.endswith(('.xml','.rels')):xml(read(n))
                return ''.join(e.text or '' for e in doc.iter() if local(e)=='t')[:MAX_TEXT],False
            if infos[0].filename!='mimetype' or infos[0].header_offset!=0 or infos[0].extra or infos[0].compress_type!=zipfile.ZIP_STORED or read('mimetype')!=b'application/epub+zip':reject()
            container=xml(read('META-INF/container.xml'));roots=[e for e in container.iter() if local(e)=='rootfile']
            if not roots:reject()
            opf_path=safe_path(roots[0].attrib.get('full-path'));opf=xml(read(opf_path))
            if local(opf)!='package':reject()
            protected='META-INF/encryption.xml' in names or 'META-INF/rights.xml' in names
            if protected:
                if 'META-INF/encryption.xml' in names:xml(read('META-INF/encryption.xml'))
                return None,True # No DRM bypass or rendering.
            items={e.attrib.get('id'):e for e in opf.iter() if local(e)=='item'};parts=[]
            for ref in opf.iter():
                if local(ref)!='itemref':continue
                item=items.get(ref.attrib.get('idref'))
                if item is None:reject()
                if item.attrib.get('media-type')!='application/xhtml+xml':continue
                href=safe_path(item.attrib.get('href'));path=posixpath.join(posixpath.dirname(opf_path),href)
                parts.append(visible(xml(read(path))))
                if sum(map(len,parts))>=MAX_TEXT:break
            return '\n'.join(parts)[:MAX_TEXT],False
    except (zipfile.BadZipFile,RuntimeError,KeyError,EOFError,NotImplementedError):reject()

def detect(body):
    if body.startswith(b'%PDF-') and re.match(rb'%PDF-[12]\.[0-9]',body[:8]) and b'%%EOF' in body[-2048:]:return 'pdf'
    if body.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(body)<33 or body[12:16]!=b'IHDR' or b'acTL' in body:reject()
        w,h=struct.unpack('>II',body[16:24])
        if not 0<w*h<=20000000 or not w or not h or b'IEND' not in body[-20:]:reject()
        return 'png'
    if body.startswith(b'\xff\xd8') and body.endswith(b'\xff\xd9'):
        # Read JPEG marker segments only; never decode pixels.
        p=2;found=False
        while p<len(body)-2:
            if body[p]!=255:reject()
            while p<len(body) and body[p]==255:p+=1
            if p>=len(body):reject()
            marker=body[p];p+=1
            if marker in (0xd9,0xda):break
            if marker in range(0xd0,0xd8):continue
            if p+2>len(body):reject()
            n=int.from_bytes(body[p:p+2],'big')
            if n<2 or p+n>len(body):reject()
            if marker in (0xc0,0xc1,0xc2):
                if n<8:reject()
                h=int.from_bytes(body[p+3:p+5],'big');w=int.from_bytes(body[p+5:p+7],'big')
                if not 0<w*h<=20000000 or not w or not h:reject()
                found=True
            p+=n
        if not found:reject()
        return 'jpg'
    if body.startswith(b'RIFF') and len(body)>=16 and body[8:12]==b'WEBP':
        if int.from_bytes(body[4:8],'little')+8!=len(body) or b'ANIM' in body:reject()
        tag=body[12:16]
        if len(body)<25 or int.from_bytes(body[16:20],'little')>len(body)-20:reject()
        if tag==b'VP8X':
            if len(body)<30 or body[20]&2:reject()
            w=int.from_bytes(body[24:27],'little')+1;h=int.from_bytes(body[27:30],'little')+1
        elif tag==b'VP8L':
            if body[20]!=47:reject()
            bits=int.from_bytes(body[21:25],'little');w=(bits&16383)+1;h=((bits>>14)&16383)+1
            if bits>>29:reject()
        elif tag==b'VP8 ':
            if len(body)<30 or body[23:26]!=b'\x9d\x01\x2a' or body[20]&1:reject()
            w=int.from_bytes(body[26:28],'little')&16383;h=int.from_bytes(body[28:30],'little')&16383
        else:reject()
        if not w or not h or w*h>20000000:reject()
        return 'webp'
    if body.startswith(b'OggS') and len(body)>27 and body[4]==0 and (b'OpusHead' in body[:4096] or b'\x01vorbis' in body[:4096]):return 'ogg'
    if body.startswith(b'fLaC') and len(body)>=42:return 'flac'
    if body.startswith(b'RIFF') and len(body)>=44 and body[8:12]==b'WAVE' and b'fmt ' in body and b'data' in body:return 'wav'
    if len(body)>20 and body[4:8]==b'ftyp' and any(b in body[8:32] for b in (b'M4A ',b'isom',b'mp42')):return 'm4a'
    offset=0
    if body.startswith(b'ID3'):
        if len(body)<10 or any(c&128 for c in body[6:10]):reject()
        offset=10+sum(c<<(7*(3-i)) for i,c in enumerate(body[6:10]))
    if len(body)>offset+4 and body[offset]==255 and body[offset+1]&0xe0==0xe0 and body[offset+2]&0xf0 not in (0,0xf0):return 'mp3'
    if body.startswith(b'PK\x03\x04'):return 'zip_container'
    reject()

def validate(body,a,kind):
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_BYTES:reject()
    ext,mime=hint(a,kind);actual=detect(body);text=None;protected=False
    if actual=='zip_container':
        if ext not in ('docx','epub'):reject()
        text,protected=package(body,ext);actual=ext
    if ext and ext!=actual:reject()
    if mime and mime!='application/octet-stream' and MIME_ALIASES.get(mime,mime)!=MIME[actual]:reject()
    if kind=='photo' and actual not in ('jpg','png','webp'):reject()
    if actual=='pdf' and b'/Encrypt' in body:protected=True
    audio=actual in ('ogg','mp3','wav','flac','m4a')
    if kind in ('voice','audio') and not audio:reject()
    return {'extension':actual,'mime_type':MIME[actual],'bytes':len(body),'audio_original':audio,
        'duration_seconds':a.get('duration'),'reading_state':'limited_protection' if protected else ('text_extracted' if text else 'original_available'),
        'source_variant':'telegram_compressed_photo' if kind=='photo' else 'original_file','protected_metadata':protected,
        'extracted_text':text}
