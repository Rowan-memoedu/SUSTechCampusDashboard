"""Read-only rendering of the installed owner app and public download page."""
from pathlib import Path
from urllib.parse import urlsplit

from sustech_dashboard.browser_services import browser_context
from sustech_dashboard.paths import DATA_ROOT, prepare_private_directory


def verify(version, output, port=18765):
    from release import save
    private=Path(output)/'private/owner-ui';prepare_private_directory(private)
    public=Path(output)/'public-ui';public.mkdir(parents=True,exist_ok=True)
    origin=f'http://127.0.0.1:{port}'
    errors=[];origins=set()
    with browser_context() as context:
        token=(DATA_ROOT/'browser-token').read_text(encoding='utf-8').strip()
        response=context.request.post(origin+'/auth/unlock',headers={'Origin':origin},data={'token':token})
        assert response.status==200, 'Local browser authentication failed'
        identity=context.request.get(origin+'/api/instance').json()
        assert identity['version']==version and identity['configured'] and not identity.get('credential_error')
        page=context.new_page();page.on('pageerror',lambda _:errors.append('local script error'))
        def readonly(route):
            request=route.request;url=urlsplit(request.url)
            origins.add(url.scheme+'://'+url.netloc)
            if request.method not in ('GET','HEAD'):route.abort()
            else:route.continue_()
        page.route('**/*',readonly)
        for width in (375,1280):
            page.set_viewport_size({'width':width,'height':900})
            page.goto(origin+'/',wait_until='networkidle')
            assert not page.url.endswith('/setup')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(private/f'local-{width}.png'),full_page=True)
        page.goto(origin+'/settings',wait_until='networkidle')
        page.screenshot(path=str(private/'settings.png'),full_page=True)
        assert origins=={origin}, 'Private UI requested external resources'
        page=context.new_page();page.on('pageerror',lambda _:errors.append('public script error'))
        for width in (320,375,414,768,1280):
            page.set_viewport_size({'width':width,'height':900})
            page.goto('https://124.221.144.155/app/',wait_until='networkidle')
            assert page.locator('.meta strong').inner_text()==version
            assert page.locator('#open-local').get_attribute('href')=='sustech-campus://open'
            assert page.locator('#open-running').get_attribute('href')==origin+'/'
            assert page.locator('input[type=password]').count()==0
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(public/f'release-{width}.png'),full_page=True)
        assert not errors, 'Browser rendering reported script errors'
    result={'passed':True,'version':version,'local_viewports':[375,1280],
            'public_viewports':[320,375,414,768,1280],'local_resources_only':True,
            'windows_launch_link':True,'running_instance_fallback':True,'script_errors':0,'school_writes':0}
    save(Path(output)/'owner-ui.json',result)
    return result
