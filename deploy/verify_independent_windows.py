"""Independent Windows acceptance using the existing private guest test agent.

Only disposable VM disks are writable. The shared base and original acceptance
credentials remain in their existing protected directory, outside the repository.
"""
import functools
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time

import requests

RPC_PORT,WEB_PORT,QMP_PORT=19676,19675,19678


def wait_browser(ps, present, timeout=180):
    """Browser launch/exit follows the backend asynchronously on a cold desktop."""
    from release import emit
    deadline=time.monotonic()+timeout
    while True:
        found=json.loads(ps('[bool](Get-Process chrome,msedge,iexplore -ErrorAction SilentlyContinue)|ConvertTo-Json -Compress'))
        if found is present:return
        if time.monotonic()>=deadline:
            raise ValueError('Normal installer did not open the browser' if present else 'Previous test browser did not close')
        emit('independent_windows',state='waiting_for_browser_launch' if present else 'waiting_for_browser_exit')
        time.sleep(3)


def qmp(message):
    with socket.create_connection(('127.0.0.1',QMP_PORT),timeout=10) as sock:
        sock.settimeout(15);stream=sock.makefile('r');stream.readline()
        def call(value):
            sock.sendall((json.dumps(value)+'\n').encode())
            while True:
                line=stream.readline()
                if not line:return {}
                value=json.loads(line)
                if 'error' in value:raise RuntimeError('VM command failed')
                if 'return' in value:return value['return']
        call({'execute':'qmp_capabilities'});return call(message)


def agent(directory):
    spec=importlib.util.spec_from_file_location('campus_acceptance_agent',directory/'vm_control.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def ensure_guest(directory, output):
    from release import emit,save
    from sustech_dashboard.paths import prepare_private_directory
    output.mkdir(parents=True,exist_ok=True)
    private=output/'private';prepare_private_directory(private)
    control=agent(directory)
    try:
        state=qmp({'execute':'query-name'})
        if state.get('name')!='Campus unified release acceptance':raise ValueError('Acceptance port belongs to another VM')
    except (ConnectionRefusedError,OSError):
        vm=output.parent/'windows-acceptance';prepare_private_directory(vm)
        overlay=vm/'overlay.qcow2';base=directory/'system.qcow2'
        if not overlay.exists():
            subprocess.run([str(directory/'qemu-native/qemu-img.exe'),'create','-f','qcow2','-F','qcow2',
                '-b',str(base),str(overlay)],check=True,capture_output=True)
            shutil.copyfile(directory/'OVMF_VARS_4M.fd',vm/'OVMF_VARS_4M.fd')
            save(vm/'base.json',{'path':str(base),'size':base.stat().st_size,'mtime_ns':base.stat().st_mtime_ns})
        baseline=json.loads((vm/'base.json').read_text())
        if (base.stat().st_size,base.stat().st_mtime_ns)!=(baseline['size'],baseline['mtime_ns']):
            raise ValueError('Shared VM base changed; keep existing overlay and prepare a new isolated acceptance disk')
        args=[str(directory/'qemu-native/qemu-system-x86_64.exe'),'-name','Campus unified release acceptance',
            '-machine','q35,accel=tcg,smm=off','-cpu','Nehalem','-smp','4','-m','4096',
            '-drive',f'file={overlay.as_posix()},format=qcow2,if=ide,index=0',
            '-drive',f'if=pflash,format=raw,readonly=on,file={(directory/"OVMF_CODE_4M.fd").as_posix()}',
            '-drive',f'if=pflash,format=raw,file={(vm/"OVMF_VARS_4M.fd").as_posix()}',
            '-boot','order=c','-display','none','-vga','std','-netdev',
            f'user,id=net0,hostfwd=tcp:127.0.0.1:{RPC_PORT}-:19476,hostfwd=tcp:127.0.0.1:{WEB_PORT}-:19475',
            '-device','e1000,netdev=net0,mac=52:54:00:ca:55:01','-qmp',f'tcp:127.0.0.1:{QMP_PORT},server=on,wait=off',
            '-rtc','base=localtime,clock=host','-monitor','none','-serial','none',
            '-device','qemu-xhci','-device','usb-tablet','-device','usb-kbd,id=acceptance-keyboard']
        with (vm/'qemu.log').open('ab') as log:
            process=subprocess.Popen(args,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        save(vm/'process.json',{'pid':process.pid,'name':'Campus unified release acceptance'})
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        try:
            info=control.request('ping',timeout=6,port=RPC_PORT)
            if info.get('ok'):
                if info.get('admin') or info.get('user')!='CampusTest':raise ValueError('Independent standard user required')
                return control,info
        except (OSError,ValueError):pass
        emit('independent_windows',state='waiting_for_existing_guest_agent');time.sleep(20)
    qmp({'execute':'screendump','arguments':{'filename':(private/'guest-pending.png').as_posix(),'format':'png'}})
    raise RuntimeError('Independent Windows guest agent is not available; evidence saved, release remains incomplete')


def verify(directory, assets, manifest, phase, output):
    from release import emit
    control,os_info=ensure_guest(directory,output)
    def ps(script):
        result=control.request('exec',script,timeout=900,port=RPC_PORT)
        if result.get('exit_code')!=0:
            # Error text may contain local account data, never send it to public CI.
            raise RuntimeError('Independent Windows command failed; phase '+phase)
        return result.get('stdout','').strip()
    s=requests.Session();s.trust_env=False;s.headers['Host']='127.0.0.1:18765'
    base=f'http://127.0.0.1:{WEB_PORT}';headers={'Origin':'http://127.0.0.1:18765'}
    def unlock():
        token=ps("Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'SUSTechCampusDashboard\\browser-token') -Raw")
        r=s.post(base+'/auth/unlock',headers=headers,json={'token':token.strip()},timeout=20);r.raise_for_status()
        page=s.get(base+'/',timeout=30);page.raise_for_status()
        headers['X-CSRF-Token']=re.search(r'const roomCsrf="([^"]+)"',page.text).group(1)
    def ready(expected=None):
        deadline=time.monotonic()+300
        while time.monotonic()<deadline:
            try:
                unlock();r=s.get(base+'/api/instance',timeout=20);r.raise_for_status();value=r.json()
                if expected is None or value['version']==expected:return value
            except requests.RequestException:pass
            time.sleep(3)
        raise RuntimeError('Independent Windows app did not become ready')
    def proof():
        return json.loads(ps(r"""$data=Join-Path $env:LOCALAPPDATA 'SUSTechCampusDashboard';$p=@{};foreach($n in @('credentials.dpapi.json','browser-token')){$file=Join-Path $data $n;if(Test-Path -LiteralPath $file){$p[$n]=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash}};$r=Join-Path $data 'downloaded-files.json';if(Test-Path $r){$receipts=Get-Content $r -Raw -Encoding UTF8|ConvertFrom-Json;foreach($item in $receipts.PSObject.Properties){$file=Join-Path (Join-Path $env:USERPROFILE 'Downloads\SUSTech') $item.Value.path;if(Test-Path -LiteralPath $file){$p['file:'+ $item.Name]=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash}}};$p|ConvertTo-Json -Compress"""))
    def stop():
        try:
            unlock();r=s.post(base+'/api/instance/stop',json={},headers=headers,timeout=20)
            if r.status_code not in (200,202):raise RuntimeError('Guest graceful stop rejected')
        except requests.ConnectionError:return
        for _ in range(90):
            if not ps('Get-Process campus-client -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id'):return
            time.sleep(2)
        raise RuntimeError('Guest operations still active; no forced stop')
    emit('independent_windows',state='checking_existing_data',phase=phase)
    initial=proof();stop()
    version=manifest['version'];record=next(a for a in manifest['assets'] if a['filename']=='windows-x86_64.zip')
    if phase=='candidate':
        # A browser left by an earlier attempt must not satisfy this installation's check.
        ps('Get-Process chrome,msedge,iexplore -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowHandle -ne 0} | ForEach-Object {$null=$_.CloseMainWindow()}')
        wait_browser(ps,False)
        installer=next(a for a in manifest['assets'] if a['filename'].endswith('.exe'))
        class Files(http.server.SimpleHTTPRequestHandler):
            def log_message(self,*_):pass
        handler=functools.partial(Files,directory=str(assets))
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url='http://10.0.2.2:'+str(server.server_port)+'/'+installer['filename']
            emit('independent_windows',state='installing_candidate',phase=phase)
            ps("$dir=Join-Path $env:LOCALAPPDATA 'CampusVmAcceptance';New-Item -ItemType Directory -Force $dir|Out-Null;"+
                "$file=Join-Path $dir 'candidate-"+version+".exe';(New-Object Net.WebClient).DownloadFile('"+url+"',$file);"+
                "if((Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne '"+installer['sha256']+"'){throw 'Candidate hash mismatch'};"+
                "$p=Start-Process $file -ArgumentList @('/SILENT','/SUPPRESSMSGBOXES','/NORESTART') -PassThru -WindowStyle Hidden;"+
                "if(!$p.WaitForExit(840000)){throw 'Installer still running'};"+
                "if($p.ExitCode -ne 0){throw 'Installer failed'};Write-Output 'Installer completed with normal automatic launch'")
        finally:server.shutdown();server.server_close()
        emit('independent_windows',state='checking_running_candidate',phase=phase)
        current=ready(version)
        wait_browser(ps,True)
    else:
        state=json.loads((output/'state.json').read_text(encoding='utf-8'))
        prior=state['previous']['windows-x86_64.zip']
        url='https://124.221.144.155/campus-updates/targets/'+prior['sha256']+'.windows-x86_64.zip'
        ps("$dir=Join-Path $env:LOCALAPPDATA 'CampusVmAcceptance';$zip=Join-Path $dir 'unified-previous.zip';"+
            "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;(New-Object Net.WebClient).DownloadFile('"+url+"',$zip);"+
            "if((Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne '"+prior['sha256']+"'){throw 'Old package hash mismatch'};"+
            "$bundle=Join-Path $dir 'unified-previous-"+prior['version']+"';if(!(Test-Path $bundle)){Expand-Archive $zip $bundle};"+
            "$env:SUSTECH_DASHBOARD_DATA_ROOT=Join-Path $env:LOCALAPPDATA 'SUSTechCampusDashboard';"+
            "$env:SUSTECH_DOWNLOAD_ROOT=Join-Path $env:USERPROFILE 'Downloads\\SUSTech';$env:SUSTECH_REGISTER_PROTOCOL='0';"+
            "Start-Process (Join-Path $bundle 'campus-client.exe') -ArgumentList @('--local-only','--no-browser') -WindowStyle Hidden;Write-Output 'Previous signed release started'")
        current=ready(prior['version']);unlock()
        r=s.post(base+'/api/instance/updates/install',json={},headers=headers,timeout=30);r.raise_for_status()
        deadline=time.monotonic()+1200
        while time.monotonic()<deadline:
            try:
                unlock();current=s.get(base+'/api/instance',timeout=20).json()
                if current.get('version')==version:break
                if current.get('update',{}).get('state')=='error':raise RuntimeError('Guest signed update failed')
            except requests.RequestException:pass
            emit('independent_windows',state='public_signed_upgrade_pending');time.sleep(10)
        else:raise TimeoutError('Guest signed update pending')
    emit('independent_windows',state='verifying_installed_payload',phase=phase)
    identity=current.get('release_identity',{})
    if (identity.get('version'),identity.get('source_commit'),identity.get('payload_sha256'))!=(version,manifest['source_commit'],record['payload_sha256']):
        raise ValueError('Guest running build differs')
    # Locate the actual child image and hash every indexed file in the guest.
    checked=json.loads(ps(r"""$process=Get-CimInstance Win32_Process -Filter "Name='campus-client.exe'" | Where-Object {$_.CommandLine -match '--backend'} | Select-Object -First 1;if(!$process){throw 'Backend process missing'};$folder=Split-Path $process.ExecutablePath;$index=Join-Path $folder 'release-files.json';$release=Get-Content (Join-Path $folder 'release.json') -Raw|ConvertFrom-Json;if((Get-FileHash $index -Algorithm SHA256).Hash.ToLowerInvariant() -ne $release.payload_sha256){throw 'Inventory mismatch'};$files=Get-Content $index -Raw|ConvertFrom-Json;$count=0;foreach($item in $files.PSObject.Properties){$path=Join-Path $folder $item.Name;if((Get-Item -LiteralPath $path).Length -ne $item.Value.size -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.Value.sha256){throw 'Installed file mismatch'};$count++};@{verified_files=$count;source_commit=$release.source_commit}|ConvertTo-Json -Compress"""))
    after=proof()
    if any(after.get(k)!=v for k,v in initial.items()):raise ValueError('Guest personal data retention changed')
    if not current.get('configured') or current.get('credential_error'):raise ValueError('Guest authentication not preserved')
    qmp({'execute':'screendump','arguments':{'filename':(output/'private'/('guest-'+phase+'.png')).as_posix(),'format':'png'}})
    return {'version':version,'source_commit':manifest['source_commit'],'artifact_sha256':record['sha256'],
        'phase':phase,'passed':True,'standard_user':True,'real_windows_guest':True,
        'verified_files':checked['verified_files'],'data_preserved':True,'configured_without_relogin':True,
        'normal_installer_browser_launch':phase=='candidate','signed_public_upgrade':phase=='upgrade','school_writes':0}
