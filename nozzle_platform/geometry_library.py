"""Immutable, content-checked local geometry records, independent of Fluent history."""
import copy,csv,hashlib,io,json,math,re,threading,time,uuid,zipfile
from pathlib import Path
from nozzle_platform.geometry_records import digest,canonical,verify_scheme,geometry_key
from nozzle_platform.design import MAX_ARRAY_PREVIEW_TRIANGLES, layout_svg
from nozzle_platform.models import MODEL_ENGINE,digest as model_digest

FILES={'scheme.json','coordinates.csv','layout.svg'}
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read_json(path):
    def reject(value):raise ValueError('几何记录包含非有限数值。')
    return json.loads(Path(path).read_text(encoding='utf-8'),parse_constant=reject)

def geometry_files(scheme):
    stream=io.StringIO(newline='');writer=csv.writer(stream,lineterminator='\n');writer.writerow(['nozzle_id','x_mm','y_mm','z_mm']);writer.writerows((i+1,*point,0) for i,point in enumerate(scheme['coordinates_mm']))
    return {'scheme.json':canonical(scheme).encode('utf-8'),'coordinates.csv':stream.getvalue().encode('utf-8-sig'),'layout.svg':layout_svg(scheme).encode('utf-8')}

class GeometryLibrary:
    def __init__(self,root,designs):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True);self.designs=designs;self.lock=threading.RLock()

    def folder(self,key):
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('几何方案库编号无效。')
        path=self.root/('entry_'+key)
        if not path.resolve().is_relative_to(self.root):raise ValueError('方案路径超出几何库。')
        return path

    def reference(self,scheme):
        ref=scheme['model'];source=ref.get('source_id');axis=ref.get('axis');origin=ref.get('origin_input_mm')
        if not isinstance(source,str) or not re.fullmatch('[0-9a-f]{64}',source) or axis not in ('x','y','z') or 'origin_input_mm' not in ref or ref.get('model_engine')!=MODEL_ENGINE:raise ValueError('原模型来源或参考坐标不完整，请重新载入模型。')
        if origin is not None and (not isinstance(origin,list) or len(origin)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in origin)):raise ValueError('记录参考点必须为三个有限数值。')
        expected=model_digest(json.dumps([MODEL_ENGINE,source,axis,origin],sort_keys=True).encode())
        if expected!=ref['model_id']:raise ValueError('模型轴线或参考点与保存模型编号不一致。')
        path=self.designs.models.root/(source+'.step')
        if not path.resolve().is_relative_to(self.designs.models.root.resolve()) or not path.is_file() or sha(path)!=source:raise ValueError('原 STEP 缺失或哈希改变；请重新导入相同 STEP，旧记录保留。')
        return ref,path

    def read(self,key):
        folder=self.folder(key);entry=read_json(folder/'entry.json')
        if entry.get('schema_version')!=1 or entry.get('entry_id')!=key or set(entry.get('files_sha256',{}))!=FILES:raise ValueError('几何记录清单不完整。')
        for name,h in entry['files_sha256'].items():
            path=folder/name
            if not path.resolve().is_relative_to(folder.resolve()) or not path.is_file() or sha(path)!=h:raise ValueError('几何记录文件缺失或哈希不一致：'+name)
        scheme=read_json(folder/'scheme.json');verify_scheme(scheme)
        if any((folder/name).read_bytes()!=raw for name,raw in geometry_files(scheme).items()):raise ValueError('坐标、图像或方案序列化与记录内容不一致。')
        if digest(dict(name=entry['name'],scheme=scheme))!=key or geometry_key(scheme)!=entry.get('geometry_key'):raise ValueError('几何方案内容与记录编号不一致。')
        self.reference(scheme)
        return entry,scheme

    def summary(self,entry,scheme):
        return dict(entry_id=entry['entry_id'],name=entry['name'],created_epoch=entry['created_epoch'],available=True,N=scheme['request']['N'],family=scheme['request']['family'],region=scheme['request']['region'],radius_mm=scheme['request']['radius_mm'],geometry_key=entry['geometry_key'],model_name=scheme['model'].get('name','STEP'),source_id=scheme['model']['source_id'],scheme_id=scheme['scheme_id'],preview_url='/geometry-library-files/'+entry['entry_id']+'/layout.svg',scientific_eligible=False)

    def catalog(self):
        def modified(path):
            try:return path.stat().st_mtime
            except OSError:return 0.
        rows=[];folders=sorted(self.root.glob('entry_*'),key=modified,reverse=True)
        for folder in folders[:100]:
            key=folder.name.removeprefix('entry_')
            try:entry,scheme=self.read(key);rows.append(self.summary(entry,scheme))
            except (ValueError,OSError,KeyError,TypeError) as error:rows.append(dict(entry_id=key,name='记录不可用',available=False,error=str(error)))
        return dict(entries=rows,total_records=len(folders),display_limit=100,scope='仅几何记录，原模型和参考坐标需匹配；不包含 CFD 结果。')

    def save(self,payload):
        if set(payload)!={'scheme_id','name'} or not isinstance(payload['name'],str) or not 1<=len(payload['name'].strip())<=60 or any(ord(c)<32 for c in payload['name']):raise ValueError('请填写 1～60 字的方案名称。')
        name=payload['name'].strip();scheme=copy.deepcopy(self.designs.get(payload['scheme_id']));verify_scheme(scheme);self.reference(scheme)
        key=digest(dict(name=name,scheme=scheme));folder=self.folder(key)
        with self.lock:
            if folder.exists():entry,stored=self.read(key);return dict(entry=self.summary(entry,stored),already_saved=True)
            folder.mkdir()
            for filename,raw in geometry_files(scheme).items():(folder/filename).write_bytes(raw)
            entry=dict(schema_version=1,entry_id=key,name=name,created_epoch=time.time(),geometry_key=geometry_key(scheme),files_sha256={n:sha(folder/n) for n in sorted(FILES)},solver_started=False)
            (folder/'entry.json').write_text(canonical(entry),encoding='utf-8')
        return dict(entry=self.summary(entry,scheme),already_saved=False)

    def load(self,key):
        entry,scheme=self.read(key);ref,path=self.reference(scheme);models=self.designs.models
        source=models.add_source(ref.get('name','single_nozzle.step'),path.read_bytes(),label=ref.get('label'),origin=ref['origin_input_mm'],archive_hash=ref.get('archive_sha256'))
        with models.lock:models.sources[source['source_id']]['default_axis']=ref['axis']
        model=models.load(source['source_id'],axis=ref['axis'],origin=ref['origin_input_mm'])
        if model['model_id']!=scheme['request']['model_id'] or model['envelope_diameter_mm']>scheme['request']['diameter_mm']+1e-6:raise ValueError('实际模型与记录包络或参考坐标不一致，未载入。')
        if 'volume_mm3' in ref and not math.isclose(model['volume_mm3'],ref['volume_mm3'],rel_tol=1e-8,abs_tol=1e-8):raise ValueError('实际模型体积与记录不一致，未载入。')
        if model['triangle_count']*scheme['request']['N']>MAX_ARRAY_PREVIEW_TRIANGLES:raise ValueError('历史方案超过当前三维预览预算，未载入。')
        verify_scheme(dict(scheme,model={k:v for k,v in model.items() if k not in {'vertices','triangles'}}))
        with self.designs.lock:self.designs.schemes[scheme['scheme_id']]=scheme
        return dict(entry=self.summary(entry,scheme),scheme=scheme,model=model,models=models.catalog(),coordinates_preserved=True,solver_started=False)

    def file(self,key,name):
        if name not in FILES|{'entry.json'}:raise ValueError('几何库文件不可用。')
        self.read(key);return self.folder(key)/name

    def export(self,key):
        entry,scheme=self.read(key);_,source=self.reference(scheme)
        files={name:(self.folder(key)/name).read_bytes() for name in sorted(FILES|{'entry.json'})}
        files['single_nozzle.step']=source.read_bytes()
        files['README.txt']='几何方案库导出\n只包含原单喷嘴 STEP、安装参考坐标、排布与坐标，不含流体域、网格或CFD结果。\n按scheme.json中的原轴线和origin_input_mm恢复模型；完整装配STEP需载入方案后单独导出。\n有限规则构造不保证全局最优；历史坐标未经当前算法重新生成。\n'.encode('utf-8')
        manifest={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}
        directory=self.root/'downloads';directory.mkdir(exist_ok=True);token=uuid.uuid4().hex;path=directory/(token+'.zip')
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            for name,raw in files.items():z.writestr(name,raw)
            z.writestr('manifest.json',canonical(manifest))
        with self.designs.lock:self.designs.downloads[token]=path
        return dict(download_url='/downloads/'+token,filename='geometry_'+key[:10]+'.zip',solver_started=False)
