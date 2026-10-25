"""Read-only, hash-bound verification bundles; never starts a solver."""
import hashlib,json,re
from pathlib import Path


def read_json(path):
    def invalid(value):raise ValueError('报告包含非有限数值。')
    return json.loads(path.read_text(encoding='utf-8'),parse_constant=invalid)


class ReviewStore:
    def __init__(self,root):self.root=Path(root).resolve()

    def checked(self,key):
        if not isinstance(key,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',key):raise ValueError('验证报告编号无效。')
        folder=(self.root/key).resolve()
        if folder.parent!=self.root or not folder.is_dir():raise ValueError('验证报告不存在。')
        manifest=read_json(folder/'manifest.json')
        if manifest.get('schema_version')!=1 or manifest.get('report_id')!=key:raise ValueError('验证报告格式不匹配。')
        files=manifest.get('files_sha256')
        if not isinstance(files,dict) or 'report.json' not in files or not 1<=len(files)<=40:raise ValueError('验证报告文件清单无效。')
        total=0
        for name,digest in files.items():
            if not re.fullmatch(r'[a-zA-Z0-9_-]+\.(json|png|csv|txt|zip)',name) or not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):raise ValueError('验证报告文件名或哈希无效。')
            path=(folder/name).resolve()
            if path.parent!=folder or not path.is_file() or path.stat().st_size>20*1024**2:raise ValueError('验证报告文件缺失或超出大小限制。')
            total+=path.stat().st_size
            if total>60*1024**2 or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('验证报告完整性检查失败，不能据此判断计算结果。')
        report=read_json(folder/'report.json')
        if report.get('report_id')!=key or report.get('scientific_eligible') is not False:raise ValueError('本入口只展示尚未获准用于正式比较的验证记录。')
        provenance=report.get('provenance',{})
        if provenance.get('audit_sha256')!=files.get('audit.json') or 'audit.json' not in files:raise ValueError('报告与原始审核来源不匹配。')
        if 'native_journal_sha256' in provenance and provenance['native_journal_sha256']!=files.get('native_journal.txt'):raise ValueError('报告与原生记录来源不匹配。')
        statuses=report.get('statuses',{})
        if set(statuses)!={'saved_fields','convergence','physical_scope'}:raise ValueError('报告缺少独立的检查状态。')
        if any(s.get('state') not in {'passed','not_met','pending','failed'} for s in statuses.values()):raise ValueError('报告检查状态无效。')
        return folder,manifest,report

    def catalog(self):
        result=[]
        for folder in sorted(self.root.iterdir(),reverse=True) if self.root.is_dir() else []:
            if not folder.is_dir() or not (folder/'manifest.json').is_file():continue
            try:
                _,_,r=self.checked(folder.name)
                result.append(dict(report_id=folder.name,title=r['title'],available=True))
            except (OSError,ValueError,KeyError,TypeError):
                result.append(dict(report_id=folder.name,title=folder.name,available=False,error='报告完整性检查未通过，请查看原始审核记录。'))
        return result

    def get(self,key):
        _,manifest,report=self.checked(key)
        return dict(report,files=[dict(name=n,url='/verification-files/'+key+'/'+n) for n in manifest['files_sha256']])

    def file(self,key,name):
        folder,manifest,_=self.checked(key)
        if name not in manifest['files_sha256']:raise ValueError('报告文件不存在。')
        return folder/name


def install_review(root,report,artifacts):
    """CLI-only publication of an immutable local bundle; no HTTP write route."""
    import shutil,zipfile
    key=report.get('report_id')
    if not isinstance(key,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',key):raise ValueError('Invalid report ID')
    if report.get('scientific_eligible') is not False:raise ValueError('Verification record must retain its limited scope')
    root=Path(root).resolve();folder=(root/key).resolve()
    if folder.parent!=root or folder.exists():raise ValueError('Preserve old reports; use a new report ID')
    if any(not re.fullmatch(r'[a-zA-Z0-9_-]+\.(json|png|csv|txt)',n) or n in {'manifest.json','report.json'} for n in artifacts):raise ValueError('Invalid artifact name')
    for path in artifacts.values():
        if not Path(path).is_file() or Path(path).stat().st_size>20*1024**2:raise ValueError('Missing or oversized artifact')
    folder.mkdir(parents=True)
    for name,path in artifacts.items():shutil.copyfile(path,folder/name)
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
    manifest=dict(schema_version=1,report_id=key,files_sha256=files)
    with zipfile.ZipFile(folder/'review_bundle.zip','w',zipfile.ZIP_DEFLATED) as bundle:
        for name in files:bundle.write(folder/name,name)
        bundle.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    files['review_bundle.zip']=hashlib.sha256((folder/'review_bundle.zip').read_bytes()).hexdigest()
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    ReviewStore(root).checked(key)
    return folder
