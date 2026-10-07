"""Follow the printing site's current authcenter/CAS flow over verified HTTPS."""
import re
from urllib.parse import urljoin, urlparse

import requests

BASE = 'https://pms.sustech.edu.cn'
PAGE = BASE + '/client/new/cprintPc/help.html'


def allowed_url(value):
    parsed = urlparse(value)
    # The school's UniEntry redirect still advertises HTTP; use the same endpoint over TLS.
    if (parsed.scheme == 'http' and parsed.hostname == 'pms.sustech.edu.cn'
            and not parsed.username and not parsed.password and parsed.port in {None,80}):
        parsed = parsed._replace(scheme='https', netloc='pms.sustech.edu.cn')
        value = parsed.geturl()
    if (parsed.scheme != 'https' or parsed.hostname not in {'pms.sustech.edu.cn', 'cas.sustech.edu.cn'}
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise ValueError('打印登录跳转地址无效')
    return value


def follow(session, value):
    for _ in range(10):
        response = session.get(allowed_url(value), allow_redirects=False, timeout=(10,40))
        if response.status_code in {301,302,303,307,308}:
            value = urljoin(value, response.headers.get('Location',''))
        else:
            response.raise_for_status()
            return response
    raise ValueError('打印登录跳转未完成')


def login():
    from sustech_survival.sso.authlib.pms import PMSAuth
    from sustech_survival.sso.authorizer import UA
    session = requests.Session()
    session.verify = True
    session.headers.update({'User-Agent':UA, 'Referer':PAGE})
    response = session.get(BASE+'/api/client/Auth/SSoPage', params={'backurl':PAGE}, timeout=(10,30))
    response.raise_for_status()
    value = response.json()
    if value.get('code') != 0 or not isinstance(value.get('result'),str):
        raise ValueError('学校打印系统暂未提供统一认证入口')
    page = follow(session, value['result'])
    if urlparse(page.url).hostname != 'cas.sustech.edu.cn':
        raise ValueError('打印登录未进入学校统一认证')
    execution = re.search(r'name="execution"\s+value="([^"]+)"', page.text)
    if not execution:
        raise ValueError('学校统一认证页面暂不可用')
    owner = PMSAuth()
    response = session.post(allowed_url(page.url), data={'username':owner.username,'password':owner.password,
        'execution':execution.group(1),'_eventId':'submit','submit':'提交'},
        allow_redirects=False, timeout=(10,40))
    if response.status_code not in {302,303} or not response.headers.get('Location'):
        raise ValueError('学校打印统一认证未成功，请检查本机校园账号')
    follow(session, urljoin(page.url,response.headers['Location']))
    session.headers.update({'Accept':'application/json', 'X-Requested-With':'XMLHttpRequest'})
    check = session.post(BASE+'/api/client/Auth/Check', timeout=(10,30))
    check.raise_for_status()
    if check.json().get('code') != 0:
        raise ValueError('学校打印账号尚未登录或开通，请在学校打印页面确认账号状态')
    return session
