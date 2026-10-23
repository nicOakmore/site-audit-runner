#!/usr/bin/env python3
"""Audit a website for broken links, SEO defects and AI-assistant visibility.

    python3 site_audit.py https://www.cumula3.com --out audit.json [--max-pages 200]

Crawls the site on its own host, records what every page carries, then checks
every link it found, internal and external, and writes one JSON file. Nothing is
judged here: the file holds measurements and the document that quotes them does
the arithmetic, so a figure in a client deliverable can be traced to a URL and a
status code.

Three groups of measurement:

  crawl       per page: status, title, meta description, headings, canonical,
              word count, images with no alt text, links out
  links       every distinct URL found, with its status and its referrers, so a
              broken link is reported with the pages that point at it
  site        robots.txt and which AI crawlers it admits, sitemap.xml and its
              URL count, llms.txt, JSON-LD types, Open Graph, the www and HTTPS
              redirects

The AI crawler list is the one that matters for being quoted by an assistant
rather than merely indexed: GPTBot and OAI-SearchBot for ChatGPT, ClaudeBot and
Claude-Web for Claude, PerplexityBot, CCBot for Common Crawl, Google-Extended
for Gemini grounding, Applebot-Extended, Bingbot for Copilot. A site can rank
well and still be invisible to all of them.
"""
import argparse
import collections
import json
import re
import time
import urllib.parse as up
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0 Safari/537.36 Cumula3SiteAudit/1.0")
HDRS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"}
AI_AGENTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-Web",
             "anthropic-ai", "PerplexityBot", "CCBot", "Google-Extended",
             "Applebot-Extended", "Bingbot", "Googlebot"]
SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "sms:", "#")


def norm(url):
    """Drop the fragment and a trailing slash, so one page is one key."""
    u = up.urldefrag(url)[0]
    if u.endswith("/") and up.urlparse(u).path != "/":
        u = u[:-1]
    return u


def same_host(url, host):
    h = (up.urlparse(url).hostname or "").lower()
    return h == host or h == "www." + host or "www." + h == host


def get(url, method="get", timeout=20):
    try:
        r = requests.request(method, url, headers=HDRS, timeout=timeout,
                             allow_redirects=True)
        return r, None
    except Exception as e:                                   # a dead host is a result
        return None, f"{type(e).__name__}: {e}"


def text_len(soup):
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return len(" ".join(soup.get_text(" ").split()).split())


def crawl(start, max_pages):
    host = (up.urlparse(start).hostname or "").lower().replace("www.", "")
    seen, queue, pages = set(), [norm(start)], {}
    links = collections.defaultdict(set)                     # url -> referrers
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        r, err = get(url)
        if err or r is None:
            pages[url] = {"status": 0, "error": err}
            continue
        ct = r.headers.get("Content-Type", "")
        rec = {"status": r.status_code, "final_url": norm(r.url),
               "redirected": norm(r.url) != url, "content_type": ct,
               "bytes": len(r.content),
               "ttfb_ms": int(r.elapsed.total_seconds() * 1000)}
        if "html" not in ct.lower() or r.status_code >= 400:
            pages[url] = rec
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        title = (soup.title.get_text(strip=True) if soup.title else "")
        desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        canon = soup.find("link", rel=lambda v: v and "canonical" in v)
        imgs = soup.find_all("img")
        # The types are usually nested inside @graph, which is how the WordPress SEO
        # plugins emit them. Reading only the top level recorded every page as "?"
        # and made a site with Organization and BreadcrumbList on every page look
        # like a site with no structured data at all.
        jsonld = []

        def walk_ld(o):
            if isinstance(o, dict):
                t = o.get("@type")
                if isinstance(t, str):
                    jsonld.append(t)
                elif isinstance(t, list):
                    jsonld.extend(x for x in t if isinstance(x, str))
                for k in ("@graph", "mainEntity", "itemListElement", "acceptedAnswer"):
                    if k in o:
                        walk_ld(o[k])
            elif isinstance(o, list):
                for i in o:
                    walk_ld(i)

        for sc in soup.find_all("script", type=lambda v: v and "ld+json" in v):
            try:
                walk_ld(json.loads(sc.string or "{}"))
            except Exception:
                jsonld.append("UNPARSEABLE")
        rec.update({
            "title": title, "title_len": len(title),
            "meta_description": (desc.get("content", "").strip() if desc else ""),
            "h1": [h.get_text(" ", strip=True) for h in soup.find_all("h1")],
            "h2_count": len(soup.find_all("h2")),
            "h3_count": len(soup.find_all("h3")),
            "canonical": canon.get("href") if canon else "",
            "words": text_len(BeautifulSoup(r.text, "html.parser")),
            "images": len(imgs),
            "images_no_alt": sum(1 for i in imgs if not (i.get("alt") or "").strip()),
            "jsonld_types": jsonld,
            "og": sorted({m.get("property") for m in
                          soup.find_all("meta", property=re.compile("^og:"))
                          if m.get("property")}),
            "viewport": bool(soup.find("meta", attrs={"name": "viewport"})),
            "lang": (soup.find("html").get("lang") if soup.find("html") else ""),
        })
        out = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(SKIP_SCHEMES):
                continue
            absu = norm(up.urljoin(r.url, href))
            if not absu.startswith("http"):
                continue
            out.append(absu)
            links[absu].add(url)
            if same_host(absu, host) and absu not in seen and absu not in queue:
                queue.append(absu)
        # A 200 that says the page does not exist. cumula3.com answers 200 on its
        # case study URLs with an H1 of "The page can't be found.", so a crawl that
        # trusts the status code counts them as live pages and every count built on
        # that population is wrong. Search engines treat these as thin duplicates.
        sig = re.compile(r"(?i)page (?:can.?t|cannot|could not) be found|"
                         r"page not found|nothing found|404 error|error 404")
        heads = " ".join(rec.get("h1", []) + [title])
        rec["soft_404"] = bool(sig.search(heads)) or (
            bool(sig.search(" ".join(soup.get_text(" ").split())[:1200]))
            and rec.get("words", 0) < 400)
        rec["links_out"] = len(out)
        rec["internal_links_out"] = sum(1 for u in out if same_host(u, host))
        pages[url] = rec
    return host, pages, links


def check_links(links, host):
    """Every distinct URL, HEAD first and GET on anything that refuses a HEAD."""
    def one(url):
        r, err = get(url, "head", timeout=15)
        if err or (r is not None and r.status_code in (403, 405, 404, 501)):
            r2, err2 = get(url, "get", timeout=20)
            if r2 is not None:
                r, err = r2, None
            elif err is None:
                err = err2
        if r is None:
            return url, {"status": 0, "error": err, "internal": same_host(url, host)}
        return url, {"status": r.status_code, "final_url": norm(r.url),
                     "redirected": norm(r.url) != url,
                     "internal": same_host(url, host)}
    out = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for url, rec in ex.map(one, list(links)):
            rec["referrers"] = sorted(links[url])[:10]
            rec["referrer_count"] = len(links[url])
            out[url] = rec
    return out


COMMON = ["about", "about-us", "contact", "contact-us", "services", "pricing",
          "blog", "insights", "resources", "careers", "team", "partners",
          "case-studies", "customers", "privacy", "privacy-policy", "terms",
          "sitemap", "netsuite-implementation", "netsuite-consulting", "support"]


def probe_common(base):
    """The paths a visitor types and an assistant guesses. A 404 here is an error
    even though nothing on the site links to it: it is a door with a number on it
    that opens onto nothing."""
    out = {}

    def one(path):
        r, err = get(f"{base}/{path}")
        return path, {"status": 0 if r is None else r.status_code,
                      "final_url": "" if r is None else norm(r.url), "error": err}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for path, rec in ex.map(one, COMMON):
            out[path] = rec
    return out


def site_checks(start, host):
    base = f"{up.urlparse(start).scheme}://{up.urlparse(start).netloc}"
    site = {"base": base, "host": host}
    for name in ("robots.txt", "sitemap.xml", "llms.txt", "sitemap_index.xml",
                 "favicon.ico"):
        r, err = get(f"{base}/{name}")
        site[name] = {"status": 0 if r is None else r.status_code,
                      "bytes": 0 if r is None else len(r.content),
                      "error": err}
        if r is not None and r.status_code == 200:
            site[name]["body_head"] = r.text[:4000]
    rb = site["robots.txt"].get("body_head", "")
    # which agents are addressed, and whether their block disallows the whole site
    agents, block, cur = {}, [], None
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
    site["robots_agents"] = {a: r for a, r in agents.items()}
    site["ai_crawlers"] = {}
    for ag in AI_AGENTS:
        rules = next((r for a, r in agents.items() if a.lower() == ag.lower()), None)
        star = next((r for a, r in agents.items() if a == "*"), [])
        used = rules if rules is not None else star
        disallowed = any(k == "disallow" and v in ("/", "/*") for k, v in used)
        site["ai_crawlers"][ag] = {
            "named_in_robots": rules is not None,
            "blocked_from_root": disallowed,
            "rules_applied": "own block" if rules is not None else "the * block",
        }
    sm = site["sitemap.xml"].get("body_head", "")
    site["sitemap_urls_in_head"] = len(re.findall(r"<loc>", sm))
    site["sitemap_is_index"] = "<sitemapindex" in sm
    for scheme_host, label in ((f"http://{host}", "http_root"),
                               (f"https://{host}", "https_root"),
                               (f"https://www.{host}", "https_www")):
        r, err = get(scheme_host)
        site[label] = {"status": 0 if r is None else r.status_code,
                       "final_url": "" if r is None else norm(r.url), "error": err}
    site["common_paths"] = probe_common(base)
    return site


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-pages", type=int, default=200)
    a = ap.parse_args()

    t0 = time.time()
    host, pages, links = crawl(a.start, a.max_pages)
    print(f"  crawled {len(pages)} pages, found {len(links)} distinct links")
    checked = check_links(links, host)
    print(f"  checked {len(checked)} links")
    site = site_checks(a.start, host)
    bad = {u: r for u, r in checked.items() if r["status"] == 0 or r["status"] >= 400}
    print(f"  {len(bad)} links are broken or unreachable")
    miss = [p for p, r in site["common_paths"].items() if r["status"] in (404, 410)]
    print(f"  {len(miss)} common paths answer 404: {', '.join(sorted(miss)[:8])}")
    json.dump({"start": a.start, "host": host, "ran": time.strftime("%Y-%m-%d %H:%M"),
               "seconds": round(time.time() - t0, 1), "pages": pages,
               "links": checked, "site": site},
              open(a.out, "w"), indent=1)
    print(f"  wrote {a.out}")


if __name__ == "__main__":
    main()
