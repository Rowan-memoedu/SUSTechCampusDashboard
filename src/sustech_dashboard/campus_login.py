"""Human CAS verification, with an owner-bound Windows DPAPI session cache."""
import hashlib
import json
import os
import threading
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from .paths import DATA_ROOT

MESSAGE = '学校要求人机验证，请在“本机与更新”中点击“恢复校园登录”'
_guard = threading.RLock()
_browser_lock = threading.Lock()
_cookies = None
_cookie_owner = None
_cookie_until = 0
_required = False
_state = {'state': 'idle', 'message': ''}


def status():
    with _guard:
        return dict(_state)


def _owner():
    from .authentication import current_credentials
    credentials = current_credentials()
    if not credentials:
        raise ValueError('请先配置校园账号')
    return hashlib.sha256(credentials[0].encode()).hexdigest()


def _stored():
    global _cookies, _cookie_owner, _cookie_until
    with _guard:
        if _cookies is not None and _cookie_owner == _owner() and _cookie_until > time.time():
            return list(_cookies)
        _cookies = []
        if os.name != 'nt':
            return []
        try:
            from .dpapi_store import unprotect_password
            value = json.loads(unprotect_password((DATA_ROOT / 'cas-session.dpapi').read_text(encoding='ascii')))
            if value['owner'] == _owner() and value['until'] > time.time():
                _cookies = value['cookies']
                _cookie_owner, _cookie_until = value['owner'], value['until']
        except (OSError, ValueError, KeyError, RuntimeError):
            pass
        return list(_cookies)


def save_session(cookies):
    global _cookies, _required, _cookie_owner, _cookie_until
    # Only the CAS domain is retained. Never mix service cookies or accounts.
    selected = [c for c in cookies if c.get('domain', '').lstrip('.') == 'cas.sustech.edu.cn'
                and c.get('secure') and (c.get('expires', -1) <= 0 or c['expires'] > time.time())]
    if not selected:
        raise ValueError('学校未提供可复用的认证会话，请重新验证')
    value = {'owner': _owner(), 'until': time.time() + 12 * 3600, 'cookies': selected}
    with _guard:
        if os.name == 'nt':
            from .dpapi_store import protect_password
            from .paths import prepare_private_directory
            prepare_private_directory()
            path = DATA_ROOT / 'cas-session.dpapi'
            temporary = path.with_suffix('.new')
            temporary.write_text(protect_password(json.dumps(value)), encoding='ascii')
            temporary.replace(path)
        _cookies = selected
        _cookie_owner, _cookie_until = value['owner'], value['until']
        _required = False
        _state.update(state='connected', message='校园登录已恢复，正在重新同步')


def challenge_required():
    global _required
    with _guard:
        _required = True
        if _state['state'] != 'waiting':
            _state.update(state='required', message=MESSAGE)
    raise ValueError(MESSAGE)


def has_challenge(html):
    soup = BeautifulSoup(html, 'html.parser')
    return bool(soup.select('input[name*="captcha" i], input[id*="captcha" i], .g-recaptcha, .h-captcha, input[name="otp"]'))


def install():
    from sustech_survival.sso.providers.cas import CASAuthorizer
    if getattr(CASAuthorizer, '_campus_interactive', False):
        return
    original = CASAuthorizer._get_ticket_cookies

    def execution(self, session):
        response = session.get(self._cas_url, headers=self._headers, timeout=self._login_timeout())
        response.raise_for_status()
        if has_challenge(response.text):
            challenge_required()
        node = BeautifulSoup(response.text, 'html.parser').select_one('input[name="execution"]')
        if not node or not node.get('value'):
            raise ValueError('学校登录页面暂不可用，请稍后恢复校园登录')
        return node['value']

    def acquire(self, username, password):
        if urlsplit(self._cas_url).hostname != 'cas.sustech.edu.cn':
            return original(self, username, password)
        if _required:
            challenge_required()
        session = self._build_cas_session()
        try:
            for cookie in _stored():
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'],
                                    path=cookie.get('path', '/'), secure=True)
            response = session.get(self._cas_url, headers=self._headers, timeout=self._login_timeout())
            response.raise_for_status()
            service = urlsplit(self.SERVICE_URL).hostname
            if urlsplit(response.url).hostname == service:
                result = {c.name: c.value for c in session.cookies
                          if service == c.domain.lstrip('.') or service.endswith('.' + c.domain.lstrip('.'))}
                if result:
                    return result
            if has_challenge(response.text):
                challenge_required()  # Stop before sending any credentials.
        finally:
            session.close()
        return original(self, username, password)

    CASAuthorizer._get_ticket_cookies = acquire
    CASAuthorizer._fetch_execution = execution
    CASAuthorizer._campus_interactive = True


def begin(runtime):
    if not _browser_lock.acquire(False):
        return
    _state.update(state='waiting', message='请在打开的学校官方窗口登录并完成人机验证；完成后会自动同步')
    def run():
        try:
            from .authentication import current_credentials
            from .browser_services import browser_context
            from sustech_survival.sso import BBAuth
            expected = current_credentials()[0]
            with browser_context(headless=False) as context:
                page = context.new_page()
                page.goto(BBAuth()._cas_url, wait_until='domcontentloaded', timeout=45000)
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline and not runtime.stop.is_set():
                    if page.is_closed():
                        raise ValueError('学校验证窗口已关闭，可重新点击恢复校园登录')
                    if urlsplit(page.url).hostname == 'bb.sustech.edu.cn':
                        response = context.request.get('https://bb.sustech.edu.cn/learn/api/public/v1/users/me', timeout=20000)
                        if response.status == 200:
                            identity = response.json()
                            if str(identity.get('userName', '')).casefold() != expected.casefold():
                                raise ValueError('学校登录账号与当前面板账号不一致，未保存会话')
                            save_session(context.cookies())
                            runtime.request_sync()
                            return
                    page.wait_for_timeout(1000)
                raise ValueError('学校验证等待结束，可重新点击恢复校园登录')
        except Exception as exc:
            with _guard:
                _state.update(state='required', message=str(exc) if isinstance(exc, ValueError) else '学校验证未完成，请重新打开验证窗口')
        finally:
            _browser_lock.release()
    worker = threading.Thread(target=run, daemon=True, name='campus-human-login')
    runtime.workers.append(worker)
    worker.start()
