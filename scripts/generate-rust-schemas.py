"""Copy existing SQL exactly; the Rust service does not load Python at runtime."""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def save(name,data):
    path=ROOT/'apps/backend-rust/migrations'/name;path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():assert path.read_bytes()==data,'Version changed schema rather than overwrite it'
    else:path.write_bytes(data)


save('paper/legacy.sql',(ROOT/'apps/api/paperpilot/schema.sql').read_bytes())
tree=ast.parse((ROOT/'apps/api/paperpilot/experiment.py').read_text(encoding='utf-8'))
for node in tree.body:
    if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA' for t in node.targets):
        save('paper/learning.sql',ast.literal_eval(node.value).encode())
for path in sorted((ROOT/'database/migrations').glob('*.sql')):save('research/'+path.name,path.read_bytes())
print('Copied eight original schema artifacts without alteration')
