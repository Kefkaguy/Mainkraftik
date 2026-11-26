"""Provision disposable local PostgreSQL DBs; no remote or production target accepted."""
import os
import secrets
import subprocess
import json
import difflib
import hashlib
import argparse
from datetime import datetime,UTC
import sys
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit

import psycopg
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from paperpilot.config import settings
from intelligence.config import settings as research_settings

SUITES=[('process_recovery_lifecycle',settings.paper_database_url,'intelligence_paper_test_rust_'),('active_engine_lifecycle',settings.paper_database_url,'intelligence_paper_test_rust_'),('safety_recovery_lifecycle',settings.paper_database_url,'intelligence_paper_test_rust_'),('research_sentiment_lifecycle',settings.paper_database_url,'intelligence_paper_test_rust_'),('audit_lifecycle',research_settings.database_url,'intelligence_test_rust_'),('evaluation_lifecycle',research_settings.database_url,'intelligence_test_rust_'),('lifecycle',settings.paper_database_url,'intelligence_paper_test_rust_'),
                               ('engine_lifecycle',settings.paper_database_url,'intelligence_paper_test_rust_'),
                               ('market_parity',settings.paper_database_url,'intelligence_paper_test_rust_'),
                               ('ranking_lifecycle',settings.paper_database_url,'intelligence_paper_test_rust_'),
                               ('discovery_lifecycle',settings.paper_database_url,'intelligence_paper_test_rust_'),
                               ('research_lifecycle',settings.paper_database_url,'intelligence_paper_test_rust_'),
                               ('api_contract',research_settings.database_url,'intelligence_test_rust_'),
                               ('write_api',research_settings.database_url,'intelligence_test_rust_'),
                               ('jobs_lifecycle',research_settings.database_url,'intelligence_test_rust_'),
                               ('research_dispatch',research_settings.database_url,'intelligence_test_rust_'),
                               ('ingestion_lifecycle',research_settings.database_url,'intelligence_test_rust_'),
                               ('forecasting_lifecycle',research_settings.database_url,'intelligence_test_rust_'),
                               ('information_lifecycle',research_settings.database_url,'intelligence_test_rust_'),
                               ('official_ir_lifecycle',research_settings.database_url,'intelligence_test_rust_'),
                               ('research_api',research_settings.database_url,'intelligence_test_rust_'),
                               ('point_features_lifecycle',research_settings.database_url,'intelligence_test_rust_'),
                               ('prospective_lifecycle',research_settings.database_url,'intelligence_test_rust_'),
                               ('verdict_lifecycle',research_settings.database_url,'intelligence_test_rust_')]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--suite',choices=[item[0] for item in SUITES])
args=parser.parse_args()
exit_code=0
for suite,configured,prefix in SUITES:
    if args.suite and suite!=args.suite:continue
    uri=urlsplit(configured)
    assert uri.hostname in ('localhost','127.0.0.1') and uri.port==5543
    name=prefix+secrets.token_hex(6);admin=urlunsplit(uri._replace(path='/postgres'))
    with psycopg.connect(admin,autocommit=True,connect_timeout=5) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        env=dict(os.environ);env['RUST_TEST_DATABASE_URL']=urlunsplit(uri._replace(path='/'+name))
        status_file=ROOT/'.local/rust-migration/native-status.json'
        env['RUST_TEST_STATUS_OUTPUT']=str(status_file)
        if suite=='research_sentiment_lifecycle':
            model=ROOT/'.local/models/distilroberta-financial-q8'
            if sys.platform=='win32':
                reference_python=ROOT/'.local/rust-migration/sentiment-oracle/Scripts/python.exe'
                library=ROOT/'.local/rust-migration/sentiment-oracle/Lib/site-packages/onnxruntime/capi/onnxruntime.dll'
                platform_name='windows'
            else:
                reference=Path.home()/'.local/share/mainkraft-rust-validation/sentiment-reference'
                reference_python=reference/'bin/python'
                libraries=list((reference/'lib/python3.12/site-packages/onnxruntime/capi').glob('libonnxruntime.so.*'));assert len(libraries)==1
                library=libraries[0];platform_name='linux'
            prepared=subprocess.run([str(reference_python),'scripts/generate-rust-research-sentiment-fixture.py','--reference-platform',platform_name],cwd=ROOT,env=env,capture_output=True,text=True)
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original real sentiment/research assembly oracle failed')
            env['RUST_SENTIMENT_MODEL_DIR']=str(model);env['RUST_SENTIMENT_LIBRARY']=str(library)
            trace=ROOT/'.local/rust-migration/sentiment-assembly-runs'/f'{name}.json';trace.parent.mkdir(parents=True,exist_ok=True)
            env['RUST_SENTIMENT_ASSEMBLY_OUTPUT']=str(trace)
        if suite=='audit_lifecycle':
            oracle=ROOT/'.local/rust-migration/audit-oracles'/f'{name}.json'
            oracle.parent.mkdir(parents=True,exist_ok=True)
            env['RUST_AUDIT_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-audit.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/audit-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated saved-head audit oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            auxiliary=oracle.with_suffix('.evaluation.json')
            reference['auxiliary']={'evaluation':{'file':auxiliary.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(auxiliary.read_bytes()).hexdigest()}}
            (ROOT/'.local/rust-migration/latest-audit-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='evaluation_lifecycle':
            oracle=ROOT/'.local/rust-migration/evaluation-oracles'/f'{name}.json'
            env['RUST_EVALUATION_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-evaluation.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/evaluation-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated evaluation oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-evaluation-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='verdict_lifecycle':
            oracle=ROOT/'.local/rust-migration/verdict-oracles'/f'{name}.json'
            env['RUST_VERDICT_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-verdict.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/verdict-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated verdict oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-verdict-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='prospective_lifecycle':
            oracle=ROOT/'.local/rust-migration/prospective-oracles'/f'{name}.json'
            env['RUST_PROSPECTIVE_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-prospective.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/prospective-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated prospective oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-prospective-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='point_features_lifecycle':
            oracle=ROOT/'.local/rust-migration/point-oracles'/f'{name}.json'
            env['RUST_POINT_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-point-features.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/point-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated point-feature oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-point-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='research_dispatch':
            oracle=ROOT/'.local/rust-migration/dispatch-oracles'/f'{name}.json'
            env['RUST_DISPATCH_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-dispatch.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/dispatch-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated dispatcher oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-dispatch-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='official_ir_lifecycle':
            oracle=ROOT/'.local/rust-migration/ir-oracles'/f'{name}.json'
            env['RUST_IR_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-official-ir.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/ir-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated official IR oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-ir-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='information_lifecycle':
            oracle=ROOT/'.local/rust-migration/information-oracles'/f'{name}.json'
            env['RUST_INFORMATION_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-information.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/information-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated information oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-information-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='forecasting_lifecycle':
            oracle=ROOT/'.local/rust-migration/forecast-oracles'/f'{name}.json'
            env['RUST_FORECAST_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-forecasting.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/forecasting-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated forecasting oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-forecast-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='ingestion_lifecycle':
            oracle=ROOT/'.local/rust-migration/ingestion-oracles'/f'{name}.json'
            env['RUST_INGESTION_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-ingestion.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/ingestion-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Original isolated ingestion oracle failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-ingestion-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='research_api':
            oracle=ROOT/'.local/rust-migration/research-api-oracles'/f'{name}.json'
            oracle.parent.mkdir(parents=True,exist_ok=True)
            env['RUST_RESEARCH_API_ORACLE']=str(oracle)
            prepared=subprocess.run([sys.executable,'scripts/prepare-rust-research-api.py'],cwd=ROOT,env=env,capture_output=True,text=True)
            (ROOT/'reports/rust-migration/research-api-python-preparation.txt').write_text(prepared.stdout+'\n'+prepared.stderr,encoding='utf-8')
            print(prepared.stdout);print(prepared.stderr)
            if prepared.returncode:raise RuntimeError('Isolated original Python API oracle preparation failed')
            reference={'file':oracle.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-research-api-oracle.json').write_text(json.dumps(reference),encoding='utf-8')
            print('ORIGINAL_RESEARCH_API_ORACLE_SHA256='+reference['sha256'])
        result=subprocess.run(['cargo','test','--locked','--manifest-path','apps/backend-rust/Cargo.toml',*(['--features','sentiment-inference'] if suite=='research_sentiment_lifecycle' else []),'--test',suite,'--','--ignored','--nocapture',*(['--exact','engine_process_death_recovery'] if suite=='process_recovery_lifecycle' else [])],cwd=ROOT,env=env,capture_output=True,text=True)
        out=ROOT/f'reports/rust-migration/postgres-{suite}.txt';out.write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        print(result.stdout);print(result.stderr)
        # A Unix signal is a negative subprocess status, never a successful suite.
        exit_code=max(exit_code,result.returncode if result.returncode>=0 else 128-result.returncode)
        if suite=='research_sentiment_lifecycle' and result.returncode==0:
            assert trace.is_file()
            reference={'file':trace.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(trace.read_bytes()).hexdigest()}
            (ROOT/'.local/rust-migration/latest-sentiment-assembly.json').write_text(json.dumps(reference),encoding='utf-8')
        if suite=='lifecycle' and result.returncode==0:
            from paperpilot import experiment
            from intelligence.util import canonical
            original=settings.paper_database_url
            try:
                settings.paper_database_url=env['RUST_TEST_DATABASE_URL']
                python=json.loads(canonical(experiment.status()));native=json.loads(status_file.read_text(encoding='utf-8'))
            finally:settings.paper_database_url=original
            def normalized(value):
                if isinstance(value,dict):return {k:normalized(v) for k,v in value.items()}
                if isinstance(value,list):return [normalized(v) for v in value]
                if isinstance(value,str) and len(value)>19 and value[4:5]=='-' and 'T' in value:
                    try:
                        instant=datetime.fromisoformat(value.replace('Z','+00:00'))
                        return instant.astimezone(UTC).isoformat(timespec='microseconds') if instant.tzinfo else value
                    except ValueError:pass
                return value
            if normalized(native)!=normalized(python):
                difference='\n'.join(difflib.unified_diff(json.dumps(normalized(python),indent=2,sort_keys=True).splitlines(),json.dumps(normalized(native),indent=2,sort_keys=True).splitlines(),fromfile='python',tofile='rust'))
                (ROOT/'reports/rust-migration/status-contract-diff.txt').write_text(difference,encoding='utf-8')
                print(difference[:3500])
                raise AssertionError('Rust/Python dashboard status contract differs')
            print('RUST_PYTHON_DASHBOARD_STATUS_PARITY=PASS')
    finally:
        with psycopg.connect(admin,autocommit=True,connect_timeout=5) as c:c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
sys.exit(exit_code)
