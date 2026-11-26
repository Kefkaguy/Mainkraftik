"""Real TLS/HTTP parity on loopback; no permitted hostname resolves externally."""
import gzip,hashlib,json,os,socket,ssl,subprocess,sys,threading,uuid,zlib
from datetime import UTC,datetime,timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.information import ir_get
directory=ROOT/'.local/rust-migration/ir-http'/str(uuid.uuid4());directory.mkdir(parents=True,exist_ok=False)
hosts=['investor.axon.com','www.motorolasolutions.com','newsroom.motorolasolutions.com','evil.invalid']
now=datetime.now(UTC);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Mainkraft isolated test CA')])
ca=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),critical=False).add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,encipher_only=None,decipher_only=None),critical=True).sign(key,hashes.SHA256())
server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
cert=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,hosts[0])])).issuer_name(name).public_key(server_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts[:3]]),critical=False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),critical=False).add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),critical=False).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False).add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=True,data_encipherment=False,key_agreement=False,key_cert_sign=False,crl_sign=False,encipher_only=None,decipher_only=None),critical=True).sign(key,hashes.SHA256())
ca_file=directory/'ca.pem';ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
(directory/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM));(directory/'server-key.pem').write_bytes(server_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
traces=[];lock=threading.Lock()
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):
        with lock:traces.append({'client':self.headers.get('x-fixture-client'),'path':self.path,'host':self.headers.get('host'),'cookie':self.headers.get('cookie'),'agent':self.headers.get('user-agent')})
        headers={};status=200;body=b'';path=self.path
        if path=='/start':status=302;headers={'Location':'/echo','Set-Cookie':'session=fixture; Path=/; Secure; HttpOnly'}
        elif path=='/clear':headers={'Set-Cookie':'session=; Max-Age=0; Path=/; Secure'};body=b'cleared'
        elif path=='/path/start':headers={'Set-Cookie':'scoped=fixture; Path=/path; Secure'};body=b'path cookie'
        elif path=='/path/shadow':headers={'Set-Cookie':'session=path; Path=/path; Secure'};body=b'overlapping cookie'
        elif path=='/multiple':headers={'Set-Cookie':['first=1; Path=/; Secure','middle=2; Path=/; Secure','last=3; Path=/; Secure']};body=b'multiple cookies'
        elif path=='/replace':headers={'Set-Cookie':'middle=replaced; Path=/; Secure'};body=b'replacement'
        elif path=='/expire-middle':headers={'Set-Cookie':'middle=; Max-Age=0; Path=/; Secure'};body=b'expired'
        elif path=='/foreign':headers={'Set-Cookie':'foreign=never; Domain=example.invalid; Path=/; Secure'};body=b'invalid foreign domain'
        elif path=='/path/default':headers={'Set-Cookie':'default=fixture; Secure'};body=b'default path'
        elif path=='/domain/start':headers={'Set-Cookie':'shared=fixture; Domain=.motorolasolutions.com; Path=/; Secure'};body=b'domain cookie'
        elif path in ['/echo','/path/echo']:body=json.dumps({'cookie':self.headers.get('cookie')},separators=(',',':')).encode()
        elif path=='/gzip':body=gzip.compress('compressed é 😀'.encode(),mtime=0);headers={'Content-Encoding':'gzip'}
        elif path=='/deflate':body=zlib.compress('compressed é 😀'.encode());headers={'Content-Encoding':'deflate'}
        elif path in ['/oversize','/gzip-limit','/gzip-oversize']:
            body=b'x'*(3*1024**2+(path!='/gzip-limit'))
            if path.startswith('/gzip'):body=gzip.compress(body,mtime=0);headers={'Content-Encoding':'gzip'}
        elif path=='/redirect-external':status=302;headers={'Location':'https://evil.invalid/must-not-be-fetched'}
        elif path=='/loop':status=302;headers={'Location':'/loop'}
        elif path=='/missing':status=404;body=b'missing'
        elif path=='/chunked':headers={'Transfer-Encoding':'chunked'};body='chunked é 😀'.encode()
        else:raise AssertionError('Unexpected test route '+path)
        self.send_response(status)
        self.send_header('Content-Type','text/html; charset=utf-8')
        for k,v in headers.items():
            for item in v if isinstance(v,list) else [v]:self.send_header(k,item)
        if 'Transfer-Encoding' not in headers:self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        try:
            if 'Transfer-Encoding' in headers:
                for part in [body[:9],body[9:11],body[11:]]:self.wfile.write(f'{len(part):x}\r\n'.encode()+part+b'\r\n')
                self.wfile.write(b'0\r\n\r\n')
            else:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError,ssl.SSLError):self.close_connection=True
class Server(ThreadingHTTPServer):daemon_threads=True
server=Server(('127.0.0.1',0),Handler);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(directory/'cert.pem',directory/'server-key.pem');server.socket=context.wrap_socket(server.socket,server_side=True)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();port=server.server_address[1]
original_resolve=socket.getaddrinfo
def resolve(host,requested_port,*args,**kwargs):
    if host in hosts:return original_resolve('127.0.0.1',port,*args,**kwargs)
    raise AssertionError('External DNS forbidden in test: '+str(host))
A='https://investor.axon.com';M='https://www.motorolasolutions.com';N='https://newsroom.motorolasolutions.com'
groups=[[A+'/start',A+'/echo'],[A+'/echo'],[A+'/start',A+'/clear',A+'/echo'],[A+'/path/start',A+'/path/echo',A+'/echo'],[M+'/domain/start',N+'/echo'],[A+'/'+p for p in ['gzip','deflate','chunked','missing','gzip-limit','oversize','gzip-oversize','redirect-external','loop']]]
groups += [[A+'/start',A+'/path/start',A+'/path/echo',A+'/echo'],[A+'/start',A+'/path/shadow',A+'/path/echo',A+'/echo']]
groups += [[A+'/multiple',A+'/echo',A+'/replace',A+'/echo',A+'/expire-middle',A+'/echo',A+'/replace',A+'/echo'],[A+'/foreign',A+'/echo'],[A+'/path/default',A+'/path/echo',A+'/echo'],[M+'/domain/start',A+'/echo',N+'/echo'],[A+'/start',M+'/echo']]
cases=[]
try:
    with patch.object(socket,'getaddrinfo',resolve):
        for urls in groups:
            group=[]
            with httpx.Client(verify=ssl.create_default_context(cafile=str(ca_file)),trust_env=False,timeout=4,follow_redirects=False,headers={'User-Agent':'fixture-ir-test','x-fixture-client':'python'}) as client:
                for url in urls:
                    try:
                        page=ir_get(client,url);outcome={'result':{'url':str(page.url),'status':page.status_code,'sha256':hashlib.sha256(page.content).hexdigest(),'text':page.text if len(page.content)<=1024 else None}}
                    except ValueError as e:outcome={'error':str(e)}
                    group.append({'url':url,**outcome})
            cases.append(group)
    fixture={'address':f'127.0.0.1:{port}','ca_file':str(ca_file),'ca_sha256':hashlib.sha256(ca_file.read_bytes()).hexdigest(),'groups':cases}
    oracle=directory/'oracle.json';oracle.write_text(json.dumps(fixture),encoding='utf-8')
    env=dict(os.environ);env['RUST_IR_TLS_ORACLE']=str(oracle)
    result=subprocess.run(['cargo','test','--manifest-path','apps/backend-rust/Cargo.toml','--lib','official_ir::tests::loopback_tls_http_cookies_compression_and_limits_match_python','--','--ignored','--nocapture'],cwd=ROOT,env=env,capture_output=True,text=True)
    print(result.stdout);print(result.stderr)
    (directory/'native.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    normalized=lambda client:[{k:v for k,v in row.items() if k!='client'} for row in traces if row['client']==client]
    expected=normalized('python');actual=normalized('rust');same=expected==actual
    report={'oracle_file':oracle.relative_to(ROOT).as_posix(),'oracle_sha256':hashlib.sha256(oracle.read_bytes()).hexdigest(),'native_exit_code':result.returncode,'requests_match':same,'python_requests':expected,'native_requests':actual,'only_loopback':True}
    (directory/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (ROOT/'.local/rust-migration/latest-ir-http.json').write_text(json.dumps({'directory':directory.relative_to(ROOT).as_posix()}),encoding='utf-8')
    assert result.returncode==0,'Native TLS comparison failed; retained evidence in '+str(directory)
    assert same,'Actual HTTP host/cookie/agent/request ordering differs; retained evidence in '+str(directory)
    print('LOOPBACK_TLS_HTTPX_NATIVE_COOKIES_REDIRECTS_COMPRESSION_CHUNKING_AND_DECODED_LIMITS=PASS')
    print(json.dumps({'groups':len(groups),'calls':sum(map(len,groups)),'requests':len(expected),'report':str(directory/'result.json')}))
finally:server.shutdown();server.server_close();thread.join(timeout=5)
