"""Read only the bounded, hashed files in a geometry-library export."""
import hashlib
import io
import json
import re
import zipfile

from nozzle_platform.models import MAX_UPLOAD_BYTES

PACKAGE_FILES={'entry.json','scheme.json','coordinates.csv','layout.svg','single_nozzle.step','README.txt'}
MAX_METADATA_BYTES=1024*1024


def document(raw):
    def reject(value):
        raise ValueError('记录包包含非有限数值。')
    value=json.loads(raw.decode('utf-8'),parse_constant=reject)
    if not isinstance(value,dict):
        raise ValueError('记录包中的 JSON 必须为对象。')
    return value


def read_package(raw):
    if not raw or len(raw)>MAX_UPLOAD_BYTES:
        raise ValueError('请选择不超过 20 MB 的几何记录 ZIP。')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members=archive.infolist()
            expected=PACKAGE_FILES|{'manifest.json'}
            if len(members)!=len(expected) or {item.filename for item in members}!=expected:
                raise ValueError('记录包文件清单不完整，请选择方案库导出的记录 ZIP。')
            files={}
            for item in members:
                limit=MAX_UPLOAD_BYTES if item.filename=='single_nozzle.step' else MAX_METADATA_BYTES
                if item.file_size>limit or item.flag_bits&1:
                    raise ValueError('记录包成员超出大小限制或已加密。')
                files[item.filename]=archive.read(item)
        manifest=document(files.pop('manifest.json'))
        if set(manifest)!=PACKAGE_FILES:
            raise ValueError('记录包哈希清单不完整。')
        for name,digest in manifest.items():
            if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest) or hashlib.sha256(files[name]).hexdigest()!=digest:
                raise ValueError('记录包文件哈希不一致：'+name)
        return files,document(files['entry.json']),document(files['scheme.json'])
    except (zipfile.BadZipFile,UnicodeError,json.JSONDecodeError,RuntimeError,NotImplementedError) as error:
        raise ValueError('无法读取几何记录 ZIP，请重新导出后再导入。') from error
