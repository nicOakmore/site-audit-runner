"""Read-only: log into wp-admin, copy the Site Health debug report and the active plugins table."""
import json, os, re, time
from playwright.sync_api import sync_playwright
site = os.environ.get("SITE", "cumula3.com"); user = os.environ["WP_USER"]; pw = os.environ["WP_PASS"]
out = {}
with sync_playwright() as p:
    br = p.chromium.launch(headless=True)
    ctx = br.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36", viewport={"width": 1366, "height": 900})
    pg = ctx.new_page()
    pg.goto(f"https://{site}/wp-login.php", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    if "One moment" in pg.title():
        pg.wait_for_function("!/One moment/i.test(document.title)", timeout=30000)
    pg.fill("#user_login", user); pg.fill("#user_pass", pw); pg.click("#wp-submit")
    pg.wait_for_load_state("domcontentloaded"); time.sleep(3)
    out["after_login_url"] = pg.url; out["after_login_title"] = pg.title()
    pg.goto(f"https://{site}/wp-admin/site-health.php?tab=debug", wait_until="domcontentloaded", timeout=60000)
    time.sleep(6)
    try:
        out["site_health_debug"] = pg.evaluate("() => { const t = document.querySelector('#health-check-debug'); return t ? t.value : ''; }")
    except Exception as e:
        out["site_health_error"] = str(e)
    if not out.get("site_health_debug"):
        out["site_health_html"] = pg.content()[:200000]
    pg.goto(f"https://{site}/wp-admin/plugins.php?plugin_status=active", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    out["active_plugins_table"] = pg.evaluate("""() => [...document.querySelectorAll('table.plugins tr[data-plugin]')].map(tr => ({
        file: tr.getAttribute('data-plugin'), slug: tr.getAttribute('data-slug'), name: (tr.querySelector('.plugin-title strong')||{}).innerText,
        version: ((tr.querySelector('.plugin-version-author-uri')||{}).innerText||'').slice(0,160),
        active: tr.classList.contains('active'), update: !!tr.querySelector('.update-message') }))""")
    pg.goto(f"https://{site}/wp-admin/plugins.php?plugin_status=inactive", wait_until="domcontentloaded", timeout=60000)
    time.sleep(2)
    out["inactive_plugins_table"] = pg.evaluate("""() => [...document.querySelectorAll('table.plugins tr[data-plugin]')].map(tr => ({
        file: tr.getAttribute('data-plugin'), name: (tr.querySelector('.plugin-title strong')||{}).innerText,
        version: ((tr.querySelector('.plugin-version-author-uri')||{}).innerText||'').slice(0,160) }))""")
    br.close()
json.dump(out, open("/out/plugins.json", "w"), indent=1)
print("done", len(out.get("site_health_debug", "")), "chars of site health;", len(out.get("active_plugins_table", [])), "active plugins")
