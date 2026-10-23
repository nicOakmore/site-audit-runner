#!/usr/bin/env python3
"""Audit a site through a real headless browser, paced, in one session.

    python3 site_audit_browser.py https://host --out audit.json \
        [--max-pages 300] [--delay 1.5] [--click] [--no-external]

Same output shape as site_audit.py (pages, links, site), so site_audit_report.py
and site_remediation_rfp.py read it unchanged, plus three blocks site_audit.py
cannot produce:

  buttons        every link, button, form, iframe and script on every page as the
                 browser rendered it, each with a verdict (see site_buttons.py)
  third_parties  every external host the site depends on, by kind and page
  ai_probe       the status each AI crawler user agent receives for /, robots.txt,
                 llms.txt and sitemap.xml, requested one at a time and paced

Why a browser and why paced. cumula3.com (GoDaddy) fronts a JavaScript
verification page ("One moment, please...") that answers HTTP 200 and, after a
burst of requests from one IP, drops the IP at the TCP level for 20 minutes or
more. Two fast crawls (12 workers) hit that on 23 October 2026 and returned 82
challenge pages and 240 connection timeouts. A headless Chromium passes the
verification, keeps its cookie for the session, and at one page every 1.5
seconds behaves like a visitor. Images, fonts and media are not downloaded, so
the request count per page stays near what a crawler sends.

If the challenge page comes back the tool waits for the self-reload, then backs
off; if the host stops answering it sleeps five minutes and resumes. Nothing is
submitted and no field is filled. With --click, controls that have no target are
clicked once and the effect recorded (see site_buttons.py for the evidence rule).
"""
import argparse
import collections
import json
import re
import sys
import time
import urllib.parse as up
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, "/Users/nico/.claude/tools")
from site_audit import AI_AGENTS, COMMON, SKIP_SCHEMES, norm, same_host  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")
CHALLENGE = re.compile(r"One moment, please|being verified|Just a moment", re.I)
SOFT404 = re.compile(r"(?i)page (?:can.?t|cannot|could not) be found|page not found|"
                     r"nothing found|404 error|error 404")
SOFT = (401, 402, 403, 405, 429, 503, 520, 521, 522, 523, 524, 525, 526)
FILE_EXT = re.compile(r"\.(pdf|png|jpe?g|webp|gif|svg|ico|mp4|mov|zip|docx?|xlsx?|pptx?|csv|woff2?|ttf|css|js|xml|txt|json)$", re.I)
DEAD_HREF = re.compile(r"^(#\S*|javascript:\s*(void\(0\)|;|)|\s*)$", re.I)

AI_UAS = {
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot",
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "ChatGPT-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "Claude-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)",
    "Claude-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +Claude-SearchBot@anthropic.com)",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
    "Perplexity-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)",
    "CCBot": "CCBot/2.0 (https://commoncrawl.org/faq/)",
    "Google-Extended": "Mozilla/5.0 (compatible; Google-Extended)",
    "Googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Applebot": "Mozilla/5.0 (compatible; Applebot/0.1; +http://www.apple.com/go/applebot)",
    "DuckAssistBot": "Mozilla/5.0 (compatible; DuckAssistBot/1.0; +http://duckduckgo.com/duckassistbot)",
    "Amazonbot": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/600.2.5 (KHTML, like Gecko) Version/8.0.2 Safari/600.2.5 (Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)",
    "meta-externalagent": "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
    "Bytespider": "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)",
    "python-requests": "python-requests/2.32",
    "curl": "curl/8.4.0",
    "Chrome, no cookie": UA,
}

JS_PAGE = r"""
() => {
  const txt = el => (el ? (el.innerText || '') : '');
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const region = el => {
    const a = el.closest('header, nav, footer, main, [role=navigation], [role=banner], [role=contentinfo]');
    if (!a) return 'body';
    const t = a.tagName.toLowerCase(), r = a.getAttribute('role');
    return (t === 'nav' || r === 'navigation') ? 'nav' : (t === 'header' || r === 'banner') ? 'header' :
           (t === 'footer' || r === 'contentinfo') ? 'footer' : 'main'; };
  const path = el => { const p = []; let e = el;
    while (e && e.nodeType === 1 && p.length < 5) {
      let s = e.tagName.toLowerCase();
      if (e.id) { s += '#' + e.id; p.unshift(s); break; }
      const cls = [...e.classList].filter(c => /elementor-(button|widget|menu|popup)|btn|cta|wp-block|menu-item|footer|header/i.test(c)).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      p.unshift(s); e = e.parentElement; }
    return p.join(' > '); };
  const meta = n => { const m = document.querySelector(`meta[name="${n}" i]`); return m ? (m.getAttribute('content') || '').trim() : ''; };
  const canon = document.querySelector('link[rel~="canonical" i]');
  const imgs = [...document.images];
  const body = document.body ? document.body.innerText : '';
  const els = [];
  for (const a of document.querySelectorAll('a')) {
    const cls = typeof a.className === 'string' ? a.className : '';
    els.push({ kind: 'link', text: (a.innerText || a.getAttribute('aria-label') || a.title || '').trim().slice(0, 80),
               href: a.getAttribute('href'), resolved: a.href || '', target_attr: a.getAttribute('target') || '',
               rel: a.getAttribute('rel') || '', classes: cls.slice(0, 120),
               looks_button: /elementor-button|btn|button|cta|wp-block-button/i.test(cls) || a.getAttribute('role') === 'button',
               onclick: !!a.getAttribute('onclick'), visible: vis(a), region: region(a), path: path(a) });
  }
  for (const b of document.querySelectorAll('button, input[type=submit], input[type=button], [role=button]:not(a)')) {
    const f = b.closest('form');
    els.push({ kind: (b.type === 'submit' || (f && !b.type)) ? 'submit' : 'button', tag: b.tagName.toLowerCase(),
               text: (b.innerText || b.value || b.getAttribute('aria-label') || b.title || '').trim().slice(0, 80),
               href: null, resolved: '', type: b.type || '', classes: (typeof b.className === 'string' ? b.className : '').slice(0, 120),
               onclick: !!b.getAttribute('onclick'), visible: vis(b), region: region(b), path: path(b), in_form: !!f,
               form_action: f ? (f.getAttribute('action') || '') : null,
               aria_controls: b.getAttribute('aria-controls') || '', aria_expanded: b.getAttribute('aria-expanded') || '' });
  }
  for (const f of document.querySelectorAll('form')) {
    const fields = [...f.querySelectorAll('input, textarea, select')].filter(x => !['hidden', 'submit', 'button'].includes(x.type));
    els.push({ kind: 'form', text: (f.getAttribute('name') || f.id || '').slice(0, 80), href: f.getAttribute('action'),
               resolved: f.action || '', method: (f.getAttribute('method') || 'get').toLowerCase(),
               classes: (typeof f.className === 'string' ? f.className : '').slice(0, 120), visible: vis(f), region: region(f), path: path(f),
               fields: fields.length, field_names: fields.map(x => x.name || x.id || x.type).slice(0, 12),
               has_submit: !!f.querySelector('button, input[type=submit]') });
  }
  for (const fr of document.querySelectorAll('iframe')) {
    els.push({ kind: 'iframe', text: (fr.title || '').slice(0, 80), href: fr.getAttribute('src'), resolved: fr.src || '',
               visible: vis(fr), region: region(fr), path: path(fr) });
  }
  return {
    title: document.title || '', meta_description: meta('description'),
    robots_meta: meta('robots'),
    h1: [...document.querySelectorAll('h1')].map(h => h.innerText.trim().replace(/\s+/g, ' ')),
    h2_count: document.querySelectorAll('h2').length, h3_count: document.querySelectorAll('h3').length,
    canonical: canon ? canon.href : '',
    words: body.split(/\s+/).filter(Boolean).length,
    first_text: body.replace(/\s+/g, ' ').slice(0, 1200),
    images: imgs.length, images_no_alt: imgs.filter(i => !(i.getAttribute('alt') || '').trim()).length,
    jsonld_raw: [...document.querySelectorAll('script[type*="ld+json" i]')].map(s => s.textContent || ''),
    og: [...new Set([...document.querySelectorAll('meta[property^="og:"]')].map(m => m.getAttribute('property')))].sort(),
    viewport: !!document.querySelector('meta[name="viewport"]'),
    lang: document.documentElement.getAttribute('lang') || '',
    generator: meta('generator'),
    elements: els,
    scripts: [...document.querySelectorAll('script[src]')].map(s => s.src),
    stylesheets: [...document.querySelectorAll('link[rel~="stylesheet"]')].map(l => l.href),
  };
}
"""


def walk_ld(o, out):
    if isinstance(o, dict):
        t = o.get("@type")
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, list):
            out.extend(x for x in t if isinstance(x, str))
        for k in ("@graph", "mainEntity", "itemListElement", "acceptedAnswer"):
            if k in o:
                walk_ld(o[k], out)
    elif isinstance(o, list):
        for i in o:
            walk_ld(i, out)


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


class Crawler:
    def __init__(self, start, max_pages, delay, click):
        self.start = norm(start)
        self.host = (up.urlparse(start).hostname or "").lower().replace("www.", "")
        self.max_pages, self.delay, self.click = max_pages, delay, click
        self.pages, self.links = {}, collections.defaultdict(set)
        self.form_targets, self.iframe_targets = collections.defaultdict(set), collections.defaultdict(set)
        self.challenges, self.outages = 0, 0

    # ---------------------------------------------------------------- fetch
    def goto(self, page, url):
        """Load a page, ride out the verification interstitial, back off on an outage."""
        for attempt in range(3):
            try:
                t0 = time.time()
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                ttfb = int((time.time() - t0) * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                if CHALLENGE.search(page.title() or ""):
                    self.challenges += 1
                    log(f"  verification page on {url}, waiting for its reload")
                    try:
                        page.wait_for_function("!/One moment|being verified|Just a moment/i.test(document.title)", timeout=20000)
                    except Exception:
                        pass
                    if CHALLENGE.search(page.title() or ""):
                        self.delay = min(self.delay * 2, 10)
                        log(f"  still challenged, delay now {self.delay}s, sleeping 90s")
                        time.sleep(90)
                        continue
                return resp, ttfb
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e)[:120]}"
                if "Timeout" in msg or "ERR_CONNECTION" in msg or "ERR_TIMED_OUT" in msg:
                    self.outages += 1
                    log(f"  host not answering ({msg}); sleeping 300s")
                    time.sleep(300)
                    continue
                return None, msg
        return None, "gave up after 3 attempts"

    # ---------------------------------------------------------------- crawl
    def crawl(self, ctx):
        queue, seen = [self.start], set()
        page = ctx.new_page()
        page.route(re.compile(r"\.(png|jpe?g|webp|gif|svg|ico|mp4|webm|woff2?|ttf|otf)(\?|$)", re.I),
                   lambda route: route.abort())
        while queue and len(self.pages) < self.max_pages:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if FILE_EXT.search(up.urlparse(url).path):
                self.links[url].add("(queued as file)")
                continue
            resp, info = self.goto(page, url)
            if resp is None:
                self.pages[url] = {"status": 0, "error": info}
                continue
            ct = resp.headers.get("content-type", "")
            rec = {"status": resp.status, "final_url": norm(page.url), "redirected": norm(page.url) != url,
                   "content_type": ct, "ttfb_ms": info}
            try:
                rec["bytes"] = len(resp.body())
            except Exception:
                rec["bytes"] = 0
            if "html" not in ct.lower() or resp.status >= 400:
                self.pages[url] = rec
                time.sleep(self.delay)
                continue
            data = page.evaluate(JS_PAGE)
            types = []
            for raw in data.pop("jsonld_raw"):
                try:
                    walk_ld(json.loads(raw or "{}"), types)
                except Exception:
                    types.append("UNPARSEABLE")
            data["jsonld_types"] = types
            data["title_len"] = len(data["title"])
            heads = " ".join(data["h1"] + [data["title"]])
            data["soft_404"] = bool(SOFT404.search(heads)) or (bool(SOFT404.search(data["first_text"])) and data["words"] < 400)
            data["challenged"] = bool(CHALLENGE.search(data["title"]))
            data.pop("first_text")
            rec.update(data)
            out = []
            for el in rec["elements"]:
                href, res = el.get("href"), el.get("resolved") or ""
                if el["kind"] == "link":
                    if href is None or href.strip().lower().startswith(SKIP_SCHEMES) or DEAD_HREF.match(href.strip()):
                        continue
                    if not res.startswith("http"):
                        continue
                    absu = norm(res)
                    out.append(absu)
                    self.links[absu].add(url)
                    if same_host(absu, self.host) and absu not in seen and absu not in queue:
                        queue.append(absu)
                elif el["kind"] == "form" and res.startswith("http"):
                    self.form_targets[norm(res)].add(url)
                elif el["kind"] == "iframe" and res.startswith("http"):
                    self.iframe_targets[norm(res)].add(url)
            rec["links_out"] = len(out)
            rec["internal_links_out"] = sum(1 for u in out if same_host(u, self.host))
            if self.click:
                self.click_tests(page, ctx, rec)
            self.pages[url] = rec
            log(f"{len(self.pages):3d} {resp.status} {url} ({len(rec['elements'])} elements, queue {len(queue)})")
            time.sleep(self.delay)
        page.close()

    # ---------------------------------------------------------------- click
    def click_tests(self, page, ctx, rec):
        start_url = page.url
        tested = 0
        for el in rec["elements"]:
            if tested >= 15:
                break
            if el["kind"] == "link":
                h = (el.get("href") or "").strip()
                if not DEAD_HREF.match(h) and h != "":
                    continue
            elif el["kind"] in ("form", "iframe") or (el["kind"] == "submit" and el.get("in_form")):
                continue
            if not el.get("visible"):
                el["click"] = "not-visible"
                continue
            tested += 1
            reqs, popups = [], []
            on_req = lambda r: reqs.append(r.url)  # noqa: E731
            on_pop = lambda pg: popups.append(pg)  # noqa: E731
            page.on("request", on_req)
            ctx.on("page", on_pop)
            try:
                handle = page.evaluate_handle(
                    "([kind, text, path]) => { const sel = kind === 'link' ? 'a' : 'button, input[type=submit], input[type=button], [role=button]:not(a)';"
                    " return [...document.querySelectorAll(sel)].find(e => (e.innerText || e.value || e.getAttribute('aria-label') || e.title || '').trim().slice(0,80) === text) || null; }",
                    [el["kind"], el["text"], el["path"]])
                node = handle.as_element()
                if node is None:
                    el["click"] = "not-found"
                    continue
                before = page.evaluate("document.querySelectorAll('*').length")
                node.click(timeout=3000, no_wait_after=True)
                time.sleep(0.8)
                dom_delta = page.evaluate("document.querySelectorAll('*').length") - before
                dialog = page.evaluate("!!document.querySelector('[role=dialog]:not([aria-hidden=true]), .elementor-popup-modal:not([style*=\"display: none\"]), .modal.show, dialog[open]')")
                expanded = page.evaluate("!!document.querySelector('[aria-expanded=true], .elementor-active, .menu-open, .is-active')")
                fired = [r for r in reqs if not re.search(r"\.(png|jpe?g|webp|gif|svg|woff2?|css|js)(\?|$)", r)]
                ev = {"requests": len(fired), "popup": bool(popups), "navigated": page.url != start_url,
                      "dom_delta": dom_delta, "dialog": dialog, "expanded": expanded}
                el["click_evidence"] = ev
                el["click"] = "handler" if (ev["popup"] or ev["navigated"] or ev["dialog"] or ev["expanded"]
                                            or dom_delta > 3 or ev["requests"]) else "nothing"
                for pg in popups:
                    try:
                        el["popup_url"] = pg.url
                        pg.close()
                    except Exception:
                        pass
                if ev["navigated"]:
                    el["navigated_to"] = page.url
                    page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
                elif dialog or dom_delta > 3 or expanded:
                    page.keyboard.press("Escape")
            except Exception as e:
                el["click"] = f"error: {type(e).__name__}: {str(e)[:100]}"
            finally:
                page.remove_listener("request", on_req)
                ctx.remove_listener("page", on_pop)

    # ---------------------------------------------------------------- links
    def check_links(self, ctx, external=True):
        """Internal targets through the browser session (cookie, paced); external
        targets with requests in parallel, since other hosts are not the one that
        rate limits us."""
        targets = set(self.links) | set(self.form_targets) | set(self.iframe_targets)
        internal = sorted(t for t in targets if same_host(t, self.host))
        ext = sorted(t for t in targets if not same_host(t, self.host))
        out = {}
        log(f"checking {len(internal)} internal targets through the browser, {len(ext)} external")
        for t in internal:
            if t in self.pages and self.pages[t].get("status"):
                p = self.pages[t]
                out[t] = {"status": p["status"], "final_url": p.get("final_url", t), "redirected": p.get("redirected", False)}
                continue
            out[t] = self._browser_head(ctx, t)
            time.sleep(max(0.5, self.delay / 2))
        if external:
            def one(u):
                try:
                    r = requests.head(u, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=15, allow_redirects=True)
                    if r.status_code in (403, 404, 405, 429, 501):
                        r = requests.get(u, headers={"User-Agent": UA, "Accept": "text/html,*/*"}, timeout=20, allow_redirects=True, stream=True)
                    return u, {"status": r.status_code, "final_url": norm(r.url), "redirected": norm(r.url) != u}
                except Exception:
                    try:                                  # a slow host, not a dead one
                        r = requests.get(u, headers={"User-Agent": UA, "Accept": "text/html,*/*"}, timeout=40, allow_redirects=True, stream=True)
                        return u, {"status": r.status_code, "final_url": norm(r.url), "redirected": norm(r.url) != u, "slow": True}
                    except Exception as e:
                        return u, {"status": 0, "error": f"{type(e).__name__}: {str(e)[:100]}"}
            with ThreadPoolExecutor(max_workers=6) as ex:
                for u, rec in ex.map(one, ext):
                    out[u] = rec
        for t, rec in out.items():
            rec["internal"] = same_host(t, self.host)
            refs = self.links.get(t, set()) | self.form_targets.get(t, set()) | self.iframe_targets.get(t, set())
            rec["referrers"] = sorted(refs)[:10]
            rec["referrer_count"] = len(refs)
            rec["kinds"] = sorted((["link"] if t in self.links else []) + (["form"] if t in self.form_targets else [])
                                  + (["iframe"] if t in self.iframe_targets else []))
        return out

    def _browser_head(self, ctx, url, ua=None):
        headers = {"User-Agent": ua} if ua else None
        for attempt in range(2):
            try:
                r = ctx.request.get(url, headers=headers, timeout=30000, max_redirects=10)
                body = r.text() if "text" in (r.headers.get("content-type") or "") else ""
                rec = {"status": r.status, "final_url": norm(r.url), "redirected": norm(r.url) != norm(url),
                       "bytes": len(r.body()), "challenge": bool(CHALLENGE.search(body[:3000]))}
                if rec["challenge"]:
                    rec["status_note"] = "200 with the verification page, not the content"
                return rec
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e)[:100]}"
                if attempt == 0 and ("Timeout" in msg or "ECONN" in msg):
                    log(f"  host not answering on {url}; sleeping 300s")
                    time.sleep(300)
                    continue
                return {"status": 0, "error": msg}

    # ----------------------------------------------------------------- site
    def site_checks(self, ctx):
        base = f"{up.urlparse(self.start).scheme}://{up.urlparse(self.start).netloc}"
        site = {"base": base, "host": self.host}
        for name in ("robots.txt", "sitemap.xml", "llms.txt", "sitemap_index.xml", "wp-sitemap.xml", "favicon.ico"):
            r = self._browser_head(ctx, f"{base}/{name}")
            site[name] = {"status": r.get("status", 0), "bytes": r.get("bytes", 0), "error": r.get("error"),
                          "challenge": r.get("challenge", False)}
            if r.get("status") == 200 and not r.get("challenge"):
                try:
                    site[name]["body_head"] = ctx.request.get(f"{base}/{name}", timeout=30000).text()[:6000]
                except Exception:
                    pass
            time.sleep(self.delay)
        rb = site["robots.txt"].get("body_head", "")
        agents, cur = {}, None
        for line in rb.splitlines():
            line = line.split("#")[0].strip()
            if not line:
                cur = None
                continue
            k, _, v = line.partition(":")
            k, v = k.strip().lower(), v.strip()
            if k == "user-agent":
                cur = v
                agents.setdefault(cur, [])
            elif cur is not None:
                agents[cur].append((k, v))
            elif k == "sitemap":
                site.setdefault("robots_sitemaps", []).append(v)
        site["robots_agents"] = agents
        site["ai_crawlers"] = {}
        for ag in AI_AGENTS:
            rules = next((r for a, r in agents.items() if a.lower() == ag.lower()), None)
            star = next((r for a, r in agents.items() if a == "*"), [])
            used = rules if rules is not None else star
            site["ai_crawlers"][ag] = {
                "named_in_robots": rules is not None,
                "blocked_from_root": any(k == "disallow" and v in ("/", "/*") for k, v in used),
                "rules_applied": "own block" if rules is not None else "the * block"}
        sm = site["sitemap.xml"].get("body_head", "")
        site["sitemap_urls_in_head"] = len(re.findall(r"<loc>", sm))
        site["sitemap_is_index"] = "<sitemapindex" in sm
        site["sitemap_locs"] = re.findall(r"<loc>\s*([^<\s]+)", sm)[:400]
        for scheme_host, label in ((f"http://{self.host}", "http_root"), (f"https://{self.host}", "https_root"),
                                   (f"https://www.{self.host}", "https_www")):
            r = self._browser_head(ctx, scheme_host)
            site[label] = {"status": r.get("status", 0), "final_url": r.get("final_url", ""), "error": r.get("error")}
            time.sleep(self.delay)
        site["common_paths"] = {}
        for path in COMMON:
            r = self._browser_head(ctx, f"{base}/{path}")
            site["common_paths"][path] = {"status": r.get("status", 0), "final_url": r.get("final_url", ""),
                                          "error": r.get("error"), "challenge": r.get("challenge", False)}
            time.sleep(self.delay)
        return site

    # ------------------------------------------------------------- ai probe
    def ai_probe(self, base):
        """Each crawler UA, one request at a time, paced, from a fresh requests
        session with no cookie, which is how a crawler arrives."""
        out = {}
        for name, ua in AI_UAS.items():
            row = {}
            for path in ("/", "/robots.txt", "/llms.txt", "/sitemap.xml"):
                try:
                    r = requests.get(base + path, headers={"User-Agent": ua}, timeout=25, allow_redirects=True)
                    body = r.text
                    row[path] = {"status": r.status_code, "bytes": len(r.content), "final_url": r.url,
                                 "verdict": "challenge" if CHALLENGE.search(body[:3000]) else
                                 ("content" if r.status_code == 200 else f"http {r.status_code}")}
                except Exception as e:
                    row[path] = {"status": 0, "error": type(e).__name__, "verdict": "no answer"}
                time.sleep(2.0)
            out[name] = row
            log(f"  probe {name:18s} " + " ".join(f"{p}={v['status']}/{v['verdict']}" for p, v in row.items()))
        return out


def classify(el, host):
    href = el.get("href")
    res = el.get("resolved") or ""
    if el["kind"] in ("button", "submit"):
        return "none"
    if href is None:
        return "none"
    h = href.strip()
    if h.lower().startswith("mailto:"):
        return "mailto"
    if h.lower().startswith("tel:"):
        return "tel"
    if DEAD_HREF.match(h):
        return "no-target"
    if not res.startswith("http"):
        return "other-scheme"
    return "internal" if same_host(res, host) else "external"


def verdicts(pages, links, host):
    """Attach a verdict to every element and build the third party register."""
    third = collections.defaultdict(lambda: {"kinds": set(), "pages": set(), "urls": set()})
    counts = collections.Counter()
    for url, rec in pages.items():
        for el in rec.get("elements", []):
            hc = classify(el, host)
            el["href_class"] = hc
            res = el.get("resolved") or ""
            if hc in ("mailto", "tel"):
                v = hc
            elif hc in ("internal", "external"):
                st = links.get(norm(res), {}).get("status", 0)
                ch = links.get(norm(res), {}).get("challenge", False)
                el["status"] = st
                v = "ok" if 200 <= st < 400 and not ch else ("blocked" if st in SOFT else ("challenged" if ch else "dead"))
            elif hc == "no-target" or el["kind"] in ("button", "submit"):
                if el["kind"] == "submit" and el.get("in_form"):
                    v = "form-submit"
                else:
                    c = el.get("click")
                    v = "handler" if c == "handler" else ("no-target" if c == "nothing" else "unclicked")
            else:
                v = hc
            el["verdict"] = v
            counts[v] += 1
            if res.startswith("http") and not same_host(res, host):
                h = (up.urlparse(res).hostname or "").lower()
                third[h]["kinds"].add(el["kind"])
                third[h]["pages"].add(url)
                third[h]["urls"].add(res[:160])
        for s in rec.get("scripts", []) + rec.get("stylesheets", []):
            if s.startswith("http") and not same_host(s, host):
                h = (up.urlparse(s).hostname or "").lower()
                third[h]["kinds"].add("script" if s in rec.get("scripts", []) else "stylesheet")
                third[h]["pages"].add(url)
                third[h]["urls"].add(s[:160])
    reg = {h: {"kinds": sorted(v["kinds"]), "page_count": len(v["pages"]), "pages": sorted(v["pages"])[:10],
               "urls": sorted(v["urls"])[:8]} for h, v in sorted(third.items())}
    return counts, reg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--click", action="store_true")
    ap.add_argument("--no-external", action="store_true")
    ap.add_argument("--no-probe", action="store_true")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright
    t0 = time.time()
    c = Crawler(a.start, a.max_pages, a.delay, a.click)
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        ctx = br.new_context(user_agent=UA, viewport={"width": 1366, "height": 900}, locale="en-US")
        c.crawl(ctx)
        log(f"crawled {len(c.pages)} pages, {len(c.links)} link targets, {c.challenges} challenges, {c.outages} outages")
        links = c.check_links(ctx, external=not a.no_external)
        site = c.site_checks(ctx)
        br.close()
    base = site["base"]
    probe = {} if a.no_probe else c.ai_probe(base)
    counts, third = verdicts(c.pages, links, c.host)
    bad = {u: r for u, r in links.items() if r.get("status", 0) == 0 or r.get("status", 0) >= 400}
    log(f"{len(bad)} targets broken or unreachable; element verdicts {dict(counts)}; {len(third)} third party hosts")
    json.dump({"start": a.start, "host": c.host, "ran": time.strftime("%Y-%m-%d %H:%M"), "tool": "site_audit_browser.py",
               "seconds": round(time.time() - t0, 1), "delay": c.delay, "challenges": c.challenges, "outages": c.outages,
               "clicked": a.click, "pages": c.pages, "links": links, "site": site,
               "element_verdicts": dict(counts), "third_parties": third, "ai_probe": probe},
              open(a.out, "w"), indent=1)
    log(f"wrote {a.out}")


if __name__ == "__main__":
    main()
