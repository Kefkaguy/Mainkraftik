"""Offline native sentiment parity against pinned local model and ONNX runtime."""
import os
import argparse
import platform
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--reference-platform',choices=['windows','linux'],default='windows')
args=parser.parse_args();assert platform.system().lower()==args.reference_platform
linux=args.reference_platform=='linux'
suffix='-linux' if linux else ''
oracle=ROOT/'.local/rust-migration/sentiment-oracle'
python=oracle/'Scripts/python.exe'
library=oracle/'Lib/site-packages/onnxruntime/capi/onnxruntime.dll'
if linux:
    validation=Path.home()/'.local/share/mainkraft-rust-validation'
    oracle=validation/'sentiment-reference';python=oracle/'bin/python'
    libraries=list((oracle/'lib/python3.12/site-packages/onnxruntime/capi').glob('libonnxruntime.so.*'));assert len(libraries)==1
    library=libraries[0]
model=ROOT/'.local/models/distilroberta-financial-q8'
assert python.is_file() and library.is_file() and model.is_dir(),'Provision the pinned local sentiment reference environment first'
result=subprocess.run([str(python),'scripts/generate-rust-sentiment-inference-fixture.py','--reference-platform',args.reference_platform],cwd=ROOT)
if result.returncode:sys.exit(result.returncode)
env=dict(os.environ)
env['RUST_SENTIMENT_REFERENCE']=str(ROOT/f'apps/backend-rust/tests/fixtures/sentiment-inference-parity{suffix}.json')
env['RUST_SENTIMENT_HTTP_REFERENCE']=str(ROOT/f'apps/backend-rust/tests/fixtures/sentiment-http-validation{suffix}.json')
if linux:
    assert 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
    env.update(CARGO_HOME=str(validation/'cargo'),RUSTUP_HOME=str(validation/'rustup'),CARGO_TARGET_DIR=str(validation/'target'),CARGO_BUILD_JOBS='2',RUSTUP_TOOLCHAIN='1.93.0',PATH=str(validation/'cargo/bin')+':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
env['RUST_SENTIMENT_MODEL_DIR']=str(model)
env['RUST_SENTIMENT_LIBRARY']=str(library)
result=subprocess.run(['cargo','test','--locked','--manifest-path','apps/backend-rust/Cargo.toml','--features','sentiment-inference','--test','sentiment_inference','--','--ignored','--nocapture'],cwd=ROOT,env=env)
if result.returncode:sys.exit(result.returncode)
result=subprocess.run(['cargo','test','--locked','--manifest-path','apps/backend-rust/Cargo.toml','--features','sentiment-inference','--lib','sentiment_worker::tests::native_http_contract_and_cancellation_keep_single_inference_job','--','--ignored','--nocapture'],cwd=ROOT,env=env)
sys.exit(result.returncode)
