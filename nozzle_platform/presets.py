"""Model-bound, immutable user settings; loading never starts a solver."""
import copy
import hashlib
import json
from pathlib import Path
import re
import threading

from nozzle_platform.models import MODEL_ENGINE,digest
from nozzle_platform.pipelines import normalize
from nozzle_platform.workflows import save
from validation import require_finite

REFERENCE_FIELDS={'source_sha256','source_unit','name','axis','origin_input_mm','model_engine'}
DESIGN_FIELDS={'model_id','N','family','region','radius_mm','start_angle_deg','sweep_angle_deg','diameter_mm',
               'edge_gap_mm','wall_clearance_mm','spacing_mode','spacing_mm','rotation_deg'}
MAX_PRESET_BYTES=64*1024


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')


def model_key(reference):
    return digest(json.dumps([MODEL_ENGINE,reference['source_sha256'],reference['axis'],reference['origin_input_mm']],sort_keys=True).encode())


def settings(document):
    if not isinstance(document,dict) or set(document)!={'schema_version','name','model_reference','request'} or type(document['schema_version']) is not int or document['schema_version']!=1:
        raise ValueError('预设格式不支持；请使用平台导出的参数 JSON。')
    result=copy.deepcopy(document);name=result['name'];ref=result['model_reference'];request=result['request']
    if not isinstance(name,str) or not 1<=len(name.strip())<=60 or any(ord(c)<32 for c in name):raise ValueError('预设名称需为 1～60 个可显示字符。')
    result['name']=name.strip()
    if not isinstance(ref,dict) or set(ref)!=REFERENCE_FIELDS or ref['model_engine']!=MODEL_ENGINE:raise ValueError('模型参考信息不完整或版本不同，请重新载入模型。')
    if not isinstance(ref['source_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',ref['source_sha256']):raise ValueError('模型来源哈希无效。')
    if ref['axis'] not in ('x','y','z') or ref['source_unit'] not in ('m','mm'):raise ValueError('模型轴线或单位无效。')
    filename=ref['name']
    if not isinstance(filename,str) or len(filename)>180 or '/' in filename or '\\' in filename or Path(filename).suffix.lower() not in ('.step','.stp'):raise ValueError('预设模型名称无效。')
    origin=ref['origin_input_mm']
    if origin is not None:
        if not isinstance(origin,list) or len(origin)!=3:raise ValueError('参考原点需为三个数值，或使用自动参考点。')
        for v in origin:require_finite(v,'参考原点')
    if not isinstance(request,dict) or not isinstance(request.get('design'),dict) or set(request['design'])!=DESIGN_FIELDS:raise ValueError('预设阵列参数不完整或包含未知字段。')
    if request['design']['model_id']!=model_key(ref):raise ValueError('预设中的模型参考坐标与阵列不一致。')
    if request['design']['region'] not in ('circle','sector') or request['design']['spacing_mode'] not in ('auto','manual'):raise ValueError('预设安装区域或定位方式无效。')
    for k in DESIGN_FIELDS-{'model_id','family','region','spacing_mode'}:require_finite(request['design'][k],k)
    domain=request.get('domain')
    if not isinstance(domain,dict) or type(domain.get('template_confirmed')) is not bool:raise ValueError('供气模板确认状态无效。')
    for k,v in domain.items():
        if k not in ('boundary_template','template_confirmed'):require_finite(v,k)
    if domain.get('boundary_template')!='axial_air_side_hydrogen':raise ValueError('预设供气模板不支持。')
    # A saved acknowledgement cannot certify a future model/installation.
    request['domain']['template_confirmed']=True
    config=normalize(request);config.pop('cases');config['domain']['template_confirmed']=False
    result['request']=config
    if len(encoded(result))>MAX_PRESET_BYTES:raise ValueError('参数预设超过 64 KiB。')
    return result


class PresetStore:
    def __init__(self,root,models,*,builtin=None):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.models=models;self.lock=threading.RLock();self.builtin_ids=set();self.builtin_problem=None
        if builtin:
            candidate=settings(json.loads(Path(builtin).read_text(encoding='utf-8')))
            source=models.root/(candidate['model_reference']['source_sha256']+'.step')
            if source.is_file():
                key=hashlib.sha256(encoded(candidate)).hexdigest();self.builtin_ids.add(key)
                try:self._store(candidate,source)
                except (OSError,ValueError) as e:self.builtin_problem=dict(preset_id=key,name=candidate['name'],available=False,builtin=True,error=str(e))

    def _folder(self,key):
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('预设编号无效。')
        return self.root/('preset_'+key)

    def _store(self,document,source):
        document=settings(document);raw=Path(source).read_bytes();ref=document['model_reference']
        if hashlib.sha256(raw).hexdigest()!=ref['source_sha256']:raise ValueError('模型文件已改变，预设未保存。')
        from engineering_cad import _step_unit
        if _step_unit(raw)!=ref['source_unit']:raise ValueError('预设单位与模型文件不同。')
        key=hashlib.sha256(encoded(document)).hexdigest();folder=self._folder(key)
        with self.lock:
            if folder.exists():self.read(key)
            else:
                folder.mkdir();(folder/'single_nozzle.step').write_bytes(raw);save(folder/'preset.json',document)
        return self.summary(key,document)

    def read(self,key):
        folder=self._folder(key);path=folder/'preset.json'
        if not path.is_file() or path.stat().st_size>MAX_PRESET_BYTES:raise ValueError('预设文件缺失或超出大小限制。')
        document=settings(json.loads(path.read_text(encoding='utf-8')))
        if hashlib.sha256(encoded(document)).hexdigest()!=key:raise ValueError('预设参数已改变，已停止载入。')
        source=folder/'single_nozzle.step'
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=document['model_reference']['source_sha256']:raise ValueError('预设原模型缺失或哈希改变，已停止载入。')
        return document

    def summary(self,key,document):
        return dict(preset_id=key,name=document['name'],model_name=document['model_reference']['name'],
            N=document['request']['design']['N'],family=document['request']['design']['family'],
            source_sha256=document['model_reference']['source_sha256'],builtin=key in self.builtin_ids,
            available=True,scientific_eligible=False)

    def catalog(self):
        items=[]
        with self.lock:
            for folder in sorted(self.root.glob('preset_*'),key=lambda p:p.stat().st_mtime,reverse=True):
                key=folder.name.removeprefix('preset_')
                try:items.append(self.summary(key,self.read(key)))
                except (ValueError,OSError,KeyError,TypeError) as e:items.append(dict(preset_id=key,name='预设不可用',available=False,error=str(e),builtin=key in self.builtin_ids))
            if self.builtin_problem and not any(i['preset_id']==self.builtin_problem['preset_id'] for i in items):items.append(self.builtin_problem)
        return items

    def store(self,name,request):
        model=self.models.get(request.get('design',{}).get('model_id'))
        if 'origin_input_mm' not in model:raise ValueError('请重新载入模型，再保存参考坐标与参数预设。')
        ref=dict(source_sha256=model['source_id'],source_unit=model['source_unit'],name=model['name'],axis=model['axis'],
                 origin_input_mm=model['origin_input_mm'],model_engine=MODEL_ENGINE)
        return self._store(dict(schema_version=1,name=name,model_reference=ref,request=request),self.models.root/(model['source_id']+'.step'))

    def load(self,key):
        document=self.read(key);ref=document['model_reference'];raw=(self._folder(key)/'single_nozzle.step').read_bytes()
        source=self.models.add_source(ref['name'],raw,origin=ref['origin_input_mm'])
        with self.models.lock:self.models.sources[source['source_id']]['default_axis']=ref['axis']
        model=self.models.load(source['source_id'],axis=ref['axis'],origin=ref['origin_input_mm'])
        if model['model_id']!=document['request']['design']['model_id']:raise ValueError('预设参考坐标未能恢复，已停止载入。')
        return dict(preset=self.summary(key,document),model=model,models=self.models.catalog(),request=document['request'])

    def import_settings(self,document):
        document=settings(document);sha=document['model_reference']['source_sha256'];source=self.models.root/(sha+'.step')
        if not source.is_file():raise ValueError('请先导入与预设匹配的 STEP 模型（SHA-256 '+sha[:12]+'…）；参数文件不包含模型实体。')
        return self._store(document,source)

    def export(self,key):
        self.read(key);return self._folder(key)/'preset.json'
