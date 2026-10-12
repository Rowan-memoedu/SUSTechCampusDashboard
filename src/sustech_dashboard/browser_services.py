"""Isolated, on-demand school browser adapters. Never attach to a user's browser."""
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
from urllib.parse import quote, urlencode, urljoin, urlsplit, parse_qsl
from bs4 import BeautifulSoup

from .actions import mark_sent
from .authentication import current_credentials
from .campus_extras import result, text_arg, integer
from .provider import _jsonable
from .core import CHINA_TZ

lock = threading.RLock()


def browser_executable():
    candidates = [os.environ.get('SUSTECH_BROWSER_EXECUTABLE', '')]
    if os.name == 'nt':
        candidates += [str(Path(os.environ.get(root, '')) / 'Google/Chrome/Application/chrome.exe')
                       for root in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
    else:
        candidates += [shutil.which(x) or '' for x in ('google-chrome', 'chromium', 'chromium-browser')]
    return next((p for p in candidates if p and Path(p).is_file()), None)


@contextmanager
def browser_context(auth=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ValueError('此功能需要浏览器组件，请安装支持扩展服务的面板版本') from None
    executable = browser_executable()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        except Exception:
            raise ValueError('未找到可用的浏览器组件；请安装 Google Chrome 后重试') from None
        try:
            context = browser.new_context(locale='zh-CN', timezone_id='Asia/Shanghai')
            if auth:
                for cookie in auth.session.cookies:
                    if cookie.domain.lstrip('.').endswith('sustech.edu.cn'):
                        context.add_cookies([{'name': cookie.name, 'value': cookie.value,
                                             'domain': cookie.domain, 'path': cookie.path or '/',
                                             'secure': cookie.secure}])
            yield context
        finally:
            browser.close()


def library(args):
    # Keep the upstream URL builder/parser, but do not inherit its silent [] on
    # navigation, auth or renderer failure (which would misreport "no books").
    from sustech_survival.lib import search as lib_search
    import importlib
    source = importlib.import_module('sustech_survival.lib.search')
    op = args.get('op', 'search')
    base = 'https://sustc.primo.exlibrisgroup.com.cn'
    if op == 'detail':
        docid = text_arg(args, 'id', 250)
        if not re.fullmatch(r'[\w.:-]+', docid):
            raise ValueError('文献标识无效')
        url = base + '/discovery/fulldisplay?' + urlencode({'docid': docid, 'vid': '86SUSTC_INST:86SUSTC', 'lang': 'zh'})
    else:
        q = text_arg(args)
        if not q:
            raise ValueError('请输入检索内容')
        field = args.get('field', 'any')
        if field not in {'any', 'title', 'creator', 'subject'}:
            raise ValueError('检索字段无效')
        kind = args.get('kind', '')
        if kind not in {'', 'Book', 'Article', 'Journal', 'Dissertation'}:
            raise ValueError('资源类型无效')
        generated = source._build_search_url(queries=[(field, 'contains', q)],
                    offset=integer(args, 'offset', 0, 2000), limit=10)
        params = dict(parse_qsl(urlsplit(generated).query))
        # The library has moved to Primo VE. The pinned builder mixes the old
        # NG host with VE's vid, which the old host rejects before redirecting.
        params.update(search_scope='MyInst_and_CI', lang='zh')
        facets = []
        if kind:
            facets.append(('facet', 'rtype,include,' + {'Book': 'books', 'Article': 'articles', 'Journal': 'journals', 'Dissertation': 'dissertations'}[kind]))
        if args.get('fulltext') == '1':
            facets.append(('facet', 'tlevel,include,online_resources'))
        if args.get('peer') == '1':
            facets.append(('facet', 'tlevel,include,peer_reviewed'))
        url = base + '/discovery/search?' + urlencode(list(params.items()) + facets)
    with browser_context() as context:
        page = context.new_page()
        page.goto(url, timeout=45000, wait_until='domcontentloaded')
        if op == 'detail':
            page.wait_for_selector('prm-full-view [data-details-label="title"]', timeout=25000)
            return result([parse_library_detail(page.content(), url)])
        page.wait_for_selector('.list-item-primary-content.result-item-primary-content, prm-no-search-result', timeout=25000)
        rows = []
        for item in page.query_selector_all('.list-item-primary-content.result-item-primary-content')[:10]:
            anchor = item.query_selector('.item-title a')
            if anchor is None:
                continue
            link = urljoin(base, anchor.get_attribute('href') or '')
            rows.append({'title': anchor.inner_text().strip(), 'url': link, 'id': source._extract_docid(link),
                         'body': item.inner_text().strip(), 'full_text': bool(item.query_selector('[class*=fulltext]'))})
        return result(rows, offset=integer(args, 'offset', 0, 2000), source=url,
                      note='全文访问取决于学校订购范围与当前网络；点击原文入口完成机构认证。')


def parse_library_detail(html, url):
    """Use Primo's field identifiers, independent of translated labels/body order."""
    soup = BeautifulSoup(html, 'html.parser')
    fields = {}
    for label in soup.select('prm-full-view [data-details-label]'):
        lines = list(label.parent.parent.stripped_strings)
        if lines and lines[0] == label.get_text(' ', strip=True):
            lines.pop(0)
        fields[label['data-details-label']] = '\n'.join(lines)
    view = soup.select_one('prm-alma-viewit')
    anchor = view.select_one('a[href]') if view else None
    title = fields.get('title', '')
    if not title:
        raise ValueError('文献详情尚未完整加载，请重试')
    return {'title': title, 'format': fields.get('type', ''),
            'authors': [v.strip() for v in fields.get('creator', '').split(';') if v.strip()],
            'publisher': fields.get('publisher', ''), 'year': fields.get('creationdate', ''),
            'language': fields.get('language', ''), 'subjects': fields.get('subject', '').splitlines(),
            'abstract': fields.get('description', ''), 'isbn': fields.get('identifier', ''),
            'full_text_availability': view.get_text(' ', strip=True) if view else '',
            'online_url': urljoin(url, anchor['href']) if anchor else '', 'detail_url': url}


@contextmanager
def cle_client():
    from sustech_survival.ehall._session import EhallSession, EHALL_BASE, EhallAuthError
    from sustech_survival.ehall.cle.client import CleClient

    class PrivateSession(EhallSession):
        def _login(self):
            credentials = current_credentials()
            if not credentials:
                raise ValueError('请先在本机设置中登录校园账号')
            req = self._state.context.request
            response = req.get(EHALL_BASE + '/amp-auth-adapter/login?service=' + quote(self.LOGIN_PROBE, safe=''), timeout=45000)
            match = re.search(r'name=["\']execution["\']\s+value=["\']([^"\']+)["\']', response.text())
            if not match:
                raise EhallAuthError('CAS login form unavailable')
            # Never fall back to or write a plaintext credential file.
            response = req.post(response.url, form={'username': credentials[0], 'password': credentials[1],
                                'execution': match.group(1), '_eventId': 'submit', 'submit': '登录'}, timeout=60000)
            if not response.ok:
                raise EhallAuthError('CAS login rejected')
            self._state.page.goto(self.CLE_INDEX, timeout=60000, wait_until='domcontentloaded')
            self._wait_app_ready()

    c = CleClient(session=PrivateSession(executable=browser_executable()), today=datetime.now(CHINA_TZ).date())
    try:
        yield c
    finally:
        c.close()


def cle(args):
    with cle_client() as c:
        op = args.get('op', 'slots')
        if op == 'mine':
            return result([{'id': r.wid, 'when': r.sksj, 'service': r.get('FWLX_DISPLAY', ''),
                            'teacher': r.get('JSXM', ''), 'room': r.get('FWZY_DISPLAY', ''),
                            'status': r.status, 'active': r.active} for r in c.my_reservations(include_inactive=True)])
        if op == 'catalog':
            return result(_jsonable(c.service_types()), teachers=_jsonable(c.teachers()), quota=c.quota().to_dict())
        slots = c.walkin(text_arg(args, 'service') or None) if op == 'walkin' else c.slots(
            day=date.fromisoformat(args['date']) if args.get('date') else None,
            days=5, service=text_arg(args, 'service') or None, teacher=text_arg(args, 'teacher') or None)
        return result([s.to_dict() for s in slots], quota=c.quota().to_dict(),
                      note='当日 walk-in 仅供到场咨询；在线预约至少提前一天，取消规则以时段预检为准。')


def prepare_cle(data):
    from sustech_survival.ehall.cle.reservation.preflight import preflight
    from sustech_survival.ehall.cle.reservation.cancel import cancel_blockers
    action = data.get('action')
    with cle_client() as c:
        if action == 'book':
            slot = c.find_slot(text_arg(data, 'id'))
            check = preflight(c, slot, now=datetime.now(CHINA_TZ).replace(tzinfo=None))
            if not check.ok:
                raise ValueError('该时段不可预约：' + '；'.join(str(b) for b in check.blockers))
            note = text_arg(data, 'note', 500)
            if not note:
                raise ValueError('请填写预约说明')
            summary = {'操作': '预约语言指导', '时段': slot.sksj, '老师': slot.teacher,
                       '指导': slot.service, '地点': slot.room, '预约说明': note,
                       '取消提示': '；'.join(check.notes)}
            target = 'cle:slot:' + slot.slot_id + ':' + slot.sksj
        elif action == 'cancel':
            reservation = c.find_reservation(text_arg(data, 'id'))
            blockers = cancel_blockers(reservation, now=datetime.now(CHINA_TZ).replace(tzinfo=None))
            if blockers:
                raise ValueError('；'.join(blockers))
            summary = {'操作': '取消语言指导预约', '时段': reservation.sksj, '预约编号': reservation.wid}
            target = 'cle:reservation:' + reservation.wid
        else:
            raise ValueError('预约操作无效')
    return {'request': dict(data), 'summary': summary, 'target': target}


def commit_cle(prepared):
    from sustech_survival.ehall.cle.reservation.wire import build_payload
    from sustech_survival.ehall.cle.reservation.submit import submit
    from sustech_survival.ehall.cle.reservation.cancel import cancel
    fresh = prepare_cle(prepared['request'])
    if fresh['summary'] != prepared['summary']:
        raise ValueError('预约状态已经变化，请重新预览')
    data = prepared['request']
    with cle_client() as c:
        if data['action'] == 'book':
            slot = c.find_slot(data['id'])
            payload = build_payload(slot, c.semester().config_id, c.identity(), note=data['note'])
            mark_sent()
            submit(c, payload)
            matched = any(r.slot_id == slot.slot_id and r.sksj == slot.sksj and r.active for r in c.my_reservations())
        else:
            mark_sent()
            cancel(c, data['id'])
            matched = any(r.wid == data['id'] and r.status_code == '4' for r in c.my_reservations(include_inactive=True))
    return {'state': 'confirmed' if matched else 'needs_review',
            'message': '学校记录已回读确认' if matched else '已发送请求，尚未在学校记录中确认；请核对我的预约，暂不重复提交。'}


def evaluation_tasks():
    from .study import auth, semester
    a, sem = auth(), semester()
    xnxq = sem.xn + sem.xq
    def rows(path, params):
        output = []
        for page in range(1, 101):
            response = a.get('/personnelEvaluation/' + path, params=dict(params, pageNum=page, pageSize=100), timeout=20)
            response.raise_for_status()
            body = response.json()
            if str(body.get('code')) != '200':
                raise RuntimeError('Evaluation list rejected')
            batch = body.get('result', {}).get('list', [])
            output.extend(batch)
            if len(batch) < 100:
                return output
        raise RuntimeError('Evaluation pagination limit')
    out = []
    for task in rows('listObtainPersonnelEvaluationTasks', {'yhdm': a.username, 'rwmc': '', 'sfyp': '0'}):
        params = {'pjrdm': a.username, 'wjid': task['firstwjid'], 'rwid': task['rwid'],
                  'bpmc': '', 'sfyp': '0', 'xnxq': xnxq, 'sfcxqbwj': '0', 'lsjgzt': ''}
        for row in rows('listEcaluationRalationshipEnriry', params):
            identity = [str(task['rwid']), str(row.get('jgwid', '')), str(row.get('rwh', '')), str(row.get('wjid', ''))]
            if not row.get('jgwid') or not row.get('wjid'):
                raise RuntimeError('Evaluation identity unavailable')
            out.append({'id': hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                        'title': row.get('kcmc') or row.get('kcmc_en') or '课程评教',
                        'course_code': row.get('kcdm', ''), 'status': str(row.get('lsjgzt', '')),
                        'task_type': task.get('rwmc', ''), 'task_wjid': task['firstwjid'],
                        'wjid': row['wjid'], 'jgwid': row['jgwid'], 'rwh': row.get('rwh', ''),
                        'xnxq': xnxq, 'task_rwid': task['rwid'], 'questionnaire_uuid': row.get('sxz', '')})
    return out


def evaluation_target(identity):
    found = next((x for x in evaluation_tasks() if x['id'] == identity), None)
    if found is None:
        raise ValueError('当前学期未找到该评教任务')
    return found


@contextmanager
def evaluation_page(target):
    from .study import auth
    a = auth()
    with browser_context(a) as context:
        page = context.new_page()
        url = 'https://tis.sustech.edu.cn/studentAssess/studentEvaluationObjectPage?' + urlencode(
            {'yhdm': a.username, 'wjid': target['task_wjid'], 'sfyp': '0', 'xnxq': target['xnxq']})
        page.goto(url, timeout=45000, wait_until='domcontentloaded')
        page.wait_for_selector('table tbody tr', timeout=20000)
        # Exact course matching; never click the first similarly named row.
        matches = []
        for row in page.query_selector_all('table tbody tr'):
            text = row.inner_text()
            if target['title'] in text and target['course_code'] and target['course_code'] in text:
                matches.append(row)
        if len(matches) != 1:
            raise ValueError('无法唯一对应官方评教表格中的课程，请在教务系统核对')
        yield page, matches[0]


def load_evaluation(target):
    from sustech_survival.tis.eval.evaluation import Evaluation
    with evaluation_page(target) as (page, row):
        button = row.query_selector('button:has-text("去评价"), button:has-text("修改")')
        if button is None:
            raise ValueError('学校当前未开放此问卷填写')
        button.click()
        page.wait_for_function('() => !!window.myVue', timeout=20000)
        ev = Evaluation(course_code=target['course_code'], course_name=target['title'],
                        task_type=target['task_type'], wjid=target['wjid'], jgwid=target['jgwid'],
                        xnxq=target['xnxq'], rwh=target['rwh'], page=page)
        ev.load()
        # Fixed upstream revision reads different attribute names in build_save_body.
        ev._jsonobj, ev._pjmap, ev._teacher_wjid = ev.jsonobj, ev.pjmap, ev.teacher_wjid
        if not ev.questions_data or not ev.jsonobj.get('wjid'):
            raise ValueError('学校问卷结构尚未完整读取，不能保存')
        ev.page = None
        return ev


def question_schema(ev):
    return [{'id': str(q['qid']), 'text': q['text'], 'type': q['type'].value, 'options': q['options']} for q in ev.questions_data]


def evaluations(args):
    if args.get('op') == 'form':
        target = evaluation_target(text_arg(args, 'id'))
        if target['status'] not in {'0', '3'}:
            raise ValueError('此问卷当前不能填写')
        ev = load_evaluation(target)
        return result(question_schema(ev), title=target['title'], id=target['id'],
                      note='请逐题填写自己的意见。面板不会预填评分；如学校已有草稿，请先核对原页面，保存会更新本问卷。')
    return result([{'id': x['id'], 'title': x['title'], 'course_code': x['course_code'],
                    'status': x['status'], 'task_type': x['task_type']} for x in evaluation_tasks()])


def prepare_evaluation(data):
    target = evaluation_target(text_arg(data, 'id'))
    action = data.get('action')
    if action == 'submit':
        if target['status'] != '3':
            raise ValueError('学校尚无已保存的问卷，请先保存并核对')
        return {'request': dict(data), 'target': 'evaluation:' + target['id'],
                'summary': {'操作': '正式提交学校已保存的问卷', '课程': target['title'],
                            '提醒': '请先核对学校已保存的回答；正式提交后可能无法修改。'}}
    if action != 'save' or target['status'] not in {'0', '3'}:
        raise ValueError('此问卷当前不能保存')
    ev = load_evaluation(target)
    schema = question_schema(ev)
    answers = data.get('answers')
    if not isinstance(answers, dict) or set(answers) != {q['id'] for q in schema}:
        raise ValueError('请完整填写当前问卷，题目变化后需要重新加载')
    summary = {'操作': '保存评教草稿', '课程': target['title']}
    for index, q in enumerate(schema):
        value = answers[q['id']]
        if q['type'] == 'rating':
            if str(value) not in [str(o) for o in q['options']]:
                raise ValueError('评分不在学校给出的选项中')
            value = int(value)
        elif q['type'] == 'text':
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 5000:
                raise ValueError('请填写评教文字回答')
        else:
            raise ValueError('此问卷包含尚不支持的题型，请在教务系统完成')
        ev.answer(index, value)
        summary[f'{index+1}. {q["text"]}'] = value
    return {'request': dict(data), 'target': 'evaluation:' + target['id'], 'summary': summary,
            'schema': schema, 'evaluation': ev}


def commit_evaluation(prepared):
    from .study import auth
    data = prepared['request']
    fresh = prepare_evaluation(data)
    if fresh['summary'] != prepared['summary'] or fresh.get('schema') != prepared.get('schema'):
        raise ValueError('问卷或状态已经变化，请重新核对')
    if data['action'] == 'save':
        ev, a = fresh['evaluation'], auth()
        prior = ''
        for layer in ('1', '2'):
            body = ev.build_save_body(pjlx=layer, prior_pjid=prior)
            if not body.get('pjjglist') or not body['pjjglist'][0].get('pjxxlist'):
                continue
            mark_sent()
            response = a.session.post(a.BASE_URL + '/personnelEvaluation/submitSaveEvaluation', json=body, timeout=30)
            response.raise_for_status()
            saved = response.json()
            if str(saved.get('code')) != '200':
                return {'state': 'needs_review', 'message': '学校未完整确认草稿保存，请核对原问卷，暂不重复保存。'}
            detail = saved.get('result') or {}
            prior = detail.get('pjid') or next((x.get('pjid') for x in detail.get('pjjglist', []) if x.get('pjid')), '')
            if layer == '1' and not prior and ev.build_save_body(pjlx='2')['pjjglist'][0]['pjxxlist']:
                return {'state': 'needs_review', 'message': '学校尚未返回后续问卷所需编号；已停止，请到教务系统核对。'}
        expected = {'3'}
    else:
        target = evaluation_target(data['id'])
        with evaluation_page(target) as (page, row):
            button = row.query_selector('button:has-text("提交")')
            if button is None:
                raise ValueError('学校当前没有提交入口')
            page.on('dialog', lambda dialog: dialog.accept())
            mark_sent()
            button.click()
            confirm = page.locator('.el-message-box button:has-text("确定"), .ivu-modal-confirm button:has-text("确定")')
            try:
                confirm.wait_for(state='visible', timeout=3000)
                if confirm.count() == 1:
                    confirm.click()
            except Exception:
                pass
            page.wait_for_timeout(1500)
        expected = {'2', '5'}
    after = evaluation_target(data['id'])
    matched = after['status'] in expected
    return {'state': 'confirmed' if matched else 'needs_review',
            'message': '学校已回读确认' + ('草稿保存' if data['action'] == 'save' else '正式提交') if matched else '已发送请求，学校状态尚未确认；请核对原页面，暂不重复提交。'}
