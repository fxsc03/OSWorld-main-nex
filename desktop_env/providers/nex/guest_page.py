"""假的 playwright Page/Locator,方法转发到 guest 内的原生 CDP 执行。
目的是让 getters/chrome.py 里那堆 config 驱动的提取逻辑一行不改 ——
逐行翻译成 JS 容易出静默偏差,而偏差会直接变成错的分数。"""
import json, logging, time
from desktop_env.providers.nex.guest_cdp import run_cdp
logger = logging.getLogger("desktopenv.guest_page")
_JS_HELPER = r'''
function __q(sel){
  if (sel.indexOf('xpath=') === 0) sel = sel.slice(6);
  if (sel.indexOf('//') === 0 || sel.indexOf('(//') === 0) {
    var r = document.evaluate(sel, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    var a = []; for (var i = 0; i < r.snapshotLength; i++) a.push(r.snapshotItem(i)); return a;
  }
  if (sel.indexOf('text=') === 0) {
    var t = sel.slice(5).replace(/^["']|["']$/g, '').trim();
    var all = Array.prototype.slice.call(document.querySelectorAll('*'));
    var exact = all.filter(function(e){ return (e.textContent||'').trim() === t && e.querySelectorAll('*').length === 0; });
    if (exact.length) return exact;
    return all.filter(function(e){ return (e.textContent||'').trim().indexOf(t) >= 0 && e.querySelectorAll('*').length === 0; });
  }
  return Array.prototype.slice.call(document.querySelectorAll(sel));
}
'''
class _Ctx:
    def __init__(self, vm_ip, server_port, tid):
        self.vm_ip, self.server_port, self.tid = vm_ip, server_port, tid
    def ev(self, jsbody, timeout=240):
        """jsbody 是函数体(自己 return)。返回 (closed, value)。"""
        js = '(function(){' + _JS_HELPER + '\n' + jsbody + '\n})()'
        body = ("tid = CONFIG['tid']\n"
                "hit = [x for x in tabs() if x.get('id') == tid]\n"
                "if not hit:\n"
                "    RESULT = {'closed': True, 'value': None}\n"
                "else:\n"
                "    RESULT = {'closed': False, 'value': evaluate(hit[0], CONFIG['js'])}\n")
        r = run_cdp(self.vm_ip, self.server_port, body, {'tid': self.tid, 'js': js}, timeout) or {}
        return bool(r.get('closed')), r.get('value')
    def val(self, jsbody, timeout=240):
        return self.ev(jsbody, timeout)[1]
def _lit(s):
    return json.dumps(s if s is not None else '')
class GuestElement:
    def __init__(self, ctx, sel, idx=0):
        self._c, self._sel, self._i = ctx, sel, idx
    def _get(self, expr):
        js = ('var a=__q(' + _lit(self._sel) + '); var i=' + str(self._i) +
              '; if(i<0) i+=a.length; var e=a[i]; return e ? (' + expr + ') : null;')
        return self._c.val(js)
    def text_content(self): return self._get('e.textContent')
    def inner_text(self): return self._get('e.innerText')
    def inner_html(self): return self._get('e.innerHTML')
    def input_value(self): return self._get('e.value')
    def get_attribute(self, name): return self._get('e.getAttribute(' + _lit(name) + ')')
    def is_visible(self): return bool(self._get('!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)'))
    def click(self): return self._get('(e.click(), true)')
class GuestLocator:
    def __init__(self, ctx, sel, idx=None):
        self._c, self._sel, self._i = ctx, sel, idx
    @property
    def first(self): return GuestLocator(self._c, self._sel, 0)
    @property
    def last(self): return GuestLocator(self._c, self._sel, -1)
    def nth(self, i): return GuestLocator(self._c, self._sel, i)
    def count(self): return int(self._c.val('return __q(' + _lit(self._sel) + ').length;') or 0)
    def all(self): return [GuestLocator(self._c, self._sel, i) for i in range(self.count())]
    def all_text_contents(self): return self._c.val('return __q(' + _lit(self._sel) + ').map(function(e){return e.textContent;});') or []
    def _el(self): return GuestElement(self._c, self._sel, self._i if self._i is not None else 0)
    def text_content(self): return self._el().text_content()
    def inner_text(self): return self._el().inner_text()
    def input_value(self): return self._el().input_value()
    def get_attribute(self, n): return self._el().get_attribute(n)
    def is_visible(self): return self._el().is_visible()
    def click(self): return self._el().click()
class GuestPage:
    def __init__(self, ctx, url=None, title=None):
        self._c, self._url, self._title = ctx, url or '', title or ''
    @property
    def url(self): return self._url
    def refresh_url(self):
        v = self._c.val('return location.href;')
        if v: self._url = v
        return self._url
    def title(self): return self._c.val('return document.title;')
    def content(self): return self._c.val('return document.documentElement.outerHTML;')
    def is_closed(self): return self._c.ev('return 1;')[0]
    def set_default_timeout(self, ms): pass
    def set_extra_http_headers(self, h): pass
    def close(self): pass
    def wait_for_load_state(self, state='load', timeout=30000):
        end = time.time() + (float(timeout or 30000) / 1000.0)
        while time.time() < end:
            if self._c.val('return document.readyState;') == 'complete':
                self.refresh_url(); return
            time.sleep(1)
        self.refresh_url()
    def wait_for_selector(self, sel, timeout=30000, **kw):
        end = time.time() + (float(timeout or 30000) / 1000.0)
        while time.time() < end:
            if int(self._c.val('return __q(' + _lit(sel) + ').length;') or 0) > 0:
                return GuestElement(self._c, sel, 0)
            time.sleep(1)
        raise TimeoutError('wait_for_selector timeout: ' + str(sel))
    def query_selector(self, sel):
        n = int(self._c.val('return __q(' + _lit(sel) + ').length;') or 0)
        return GuestElement(self._c, sel, 0) if n else None
    def query_selector_all(self, sel):
        n = int(self._c.val('return __q(' + _lit(sel) + ').length;') or 0)
        return [GuestElement(self._c, sel, i) for i in range(n)]
    def locator(self, sel): return GuestLocator(self._c, sel)
    def evaluate(self, js, *a): return self._c.val('return (' + js + ');')
    def goto(self, url, **kw):
        self._c.val('location.href = ' + _lit(url) + '; return true;')
        time.sleep(3)
        self.wait_for_load_state(timeout=kw.get('timeout', 60000))
    def click(self, sel, **kw):
        e = self.query_selector(sel)
        return e.click() if e else None
    def fill(self, sel, val, **kw):
        return self._c.val('var a=__q(' + _lit(sel) + '); if(a[0]){a[0].value=' + _lit(val) + '; a[0].dispatchEvent(new Event("input",{bubbles:true}));} return true;')
class _FakeContext:
    def __init__(self, br): self._br = br
    @property
    def pages(self): return list(self._br._pages)
    def new_page(self): return self._br.new_page()
    def close(self): pass
class GuestBrowser:
    """只提供 getters 用到的 contexts/pages。"""
    def __init__(self, vm_ip, server_port):
        self.vm_ip, self.server_port = vm_ip, server_port
        body = "RESULT = [{'id': t.get('id'), 'url': t.get('url'), 'title': t.get('title')} for t in tabs()]\n"
        lst = run_cdp(vm_ip, server_port, body) or []
        self._pages = [GuestPage(_Ctx(vm_ip, server_port, t['id']), t.get('url'), t.get('title')) for t in lst if t.get('id')]
        logger.info('[guest-page] 发现 %s 个标签页: %s', len(self._pages), [p.url for p in self._pages])
    @property
    def contexts(self): return [_FakeContext(self)]
    @property
    def pages(self): return list(self._pages)
    def new_page(self):
        body = ("t = open_tab('about:blank')\n"
                "RESULT = {'id': (t or {}).get('id'), 'url': (t or {}).get('url')}\n")
        r = run_cdp(self.vm_ip, self.server_port, body) or {}
        if not r.get('id'):
            raise RuntimeError('guest new_page 失败')
        pg = GuestPage(_Ctx(self.vm_ip, self.server_port, r['id']), r.get('url'), '')
        self._pages.append(pg)
        return pg
    def close(self): pass
def guest_browser(env):
    return GuestBrowser(env.vm_ip, env.server_port)
