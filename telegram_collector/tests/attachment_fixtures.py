import io,zipfile,struct,zlib,wave

def pdf():
 parts=[b'%PDF-1.4\n'];offsets=[0]
 objs=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
 stream=b'BT /F1 12 Tf 20 100 Td (IRINA SYNTHETIC ATTACHMENT TEST) Tj ET';objs.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
 for i,o in enumerate(objs,1):offsets.append(sum(map(len,parts)));parts.append(str(i).encode()+b' 0 obj\n'+o+b'\nendobj\n')
 xref=sum(map(len,parts));parts.append(b'xref\n0 6\n0000000000 65535 f \n'+b''.join(('%010d 00000 n \n'%n).encode() for n in offsets[1:]));parts.append(b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n');return b''.join(parts)
def zipbody(files):
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_DEFLATED) as z:
  for n,v in files.items():z.writestr(n,v,compress_type=zipfile.ZIP_STORED if n=='mimetype' else zipfile.ZIP_DEFLATED)
 return b.getvalue()
def docx(extra=None):
 f={'[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>', '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>','word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>IRINA SYNTHETIC ATTACHMENT TEST</w:t></w:r></w:p></w:body></w:document>'};f.update(extra or {});return zipbody(f)
def epub(extra=None,protected=False):
 f={'mimetype':'application/epub+zip','META-INF/container.xml':'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>','OEBPS/package.opf':'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">synthetic</dc:identifier><dc:title>Irina Test</dc:title><dc:language>it</dc:language></metadata><manifest><item id="c" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c"/></spine></package>','OEBPS/chapter.xhtml':'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test</title><script>SHOULD_NOT_EXTRACT_OR_RUN</script></head><body><p>IRINA SYNTHETIC ATTACHMENT TEST</p><img src="https://invalid.test/tracker"/></body></html>'}
 if protected:f['META-INF/encryption.xml']='<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData/></encryption>'
 f.update(extra or {});return zipbody(f)
def png():
 def chunk(t,b):return struct.pack('>I',len(b))+t+b+struct.pack('>I',zlib.crc32(t+b)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\x10\x20\x30'))+chunk(b'IEND',b'')
def wav():
 b=io.BytesIO()
 with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\x00\x00'*800)
 return b.getvalue()
def ogg():return b'OggS'+b'\x00'*23+b'OpusHead'+b'\x01\x01'+b'\x00'*30
