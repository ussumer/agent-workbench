import argparse,hashlib,importlib.metadata,json,shutil,subprocess
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.markers import default_environment
p=argparse.ArgumentParser();p.add_argument('--context',type=Path,required=True);a=p.parse_args()
root=Path('/mnt/c/dev/rush-harness/.venv/lib/python3.12/site-packages');a.context.mkdir(exist_ok=False)
installed={canonicalize_name(d.metadata['Name']):d for d in importlib.metadata.distributions(path=[str(root)])}
needed={'opensandbox-server':set()};processed={};files={};packages=[]
while True:
 pending=[n for n in needed if processed.get(n)!=needed[n]]
 if not pending:break
 for name in pending:
  d=installed[name];extras=needed[name];processed[name]=set(extras)
  for line in d.requires or []:
   r=Requirement(line)
   if r.marker and not any(r.marker.evaluate({**default_environment(),'extra':e}) for e in ('',*extras)):continue
   target=canonicalize_name(r.name)
   if target not in installed or (r.specifier and not r.specifier.contains(installed[target].version)):raise ValueError('installed dependency mismatch: '+str(r))
   needed.setdefault(target,set()).update(r.extras)
for name in sorted(needed):
 d=installed[name];local={}
 for f in d.files or []:
  source=Path(d.locate_file(f)).resolve()
  if not source.is_relative_to(root) or '__pycache__' in source.parts:continue
  if not source.is_file():raise FileNotFoundError(str(source))
  rel=source.relative_to(root).as_posix();sha=hashlib.sha256(source.read_bytes()).hexdigest();local[rel]=sha;files[rel]=sha
  target=a.context/'dependencies'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
 packages.append({'name':name,'version':d.version,'files_sha256':hashlib.sha256(json.dumps(local,sort_keys=True).encode()).hexdigest()})
sha=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
base=json.loads(subprocess.check_output(['docker','image','inspect','python:3.12-slim'],text=True))[0]
manifest={'server_version':installed['opensandbox-server'].version,'base_image':base['RepoDigests'][0],'source_snapshot_sha256':sha,'packages':packages}
lock=Path(__file__).with_name('requirements.lock.json')
if lock.exists() and json.loads(lock.read_text())!=manifest:raise ValueError('installed snapshot differs from lock')
lock.write_text(json.dumps(manifest,indent=2)+'\n');(a.context/'snapshot.json').write_text(json.dumps({**manifest,'files':files},indent=2)+'\n')
text=Path(__file__).with_name('Dockerfile').read_text().replace('__BASE_IMAGE__',manifest['base_image']).replace('__SOURCE_SHA__',sha).replace('__SERVER_VERSION__',manifest['server_version']);(a.context/'Dockerfile').write_text(text)
print(json.dumps({'status':'packaged','packages':len(packages),'files':len(files),'source_snapshot_sha256':sha}))
