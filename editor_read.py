"""Read-only: read plugin source through wp-admin's plugin file editor, which needs only the WP login."""
import json, os, time, urllib.parse as up
from playwright.sync_api import sync_playwright
site = os.environ.get("SITE", "cumula3.com"); user = os.environ["WP_USER"]; pw = os.environ["WP_PASS"]
mains = os.environ.get("PLUGIN_FILES", "").split(",")
out = {"files": {}, "errors": []}
with sync_playwright() as p:
    br = p.chromium.launch(headless=True)
    ctx = br.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")
    pg = ctx.new_page()
    pg.goto(f"https://{site}/wp-login.php", wait_until="domcontentloaded", timeout=60000); time.sleep(3)
    pg.fill("#user_login", user); pg.fill("#user_pass", pw); pg.click("#wp-submit"); pg.wait_for_load_state("domcontentloaded"); time.sleep(3)
    out["login"] = pg.url
    for main in mains:
        if not main: continue
        try:
            pg.goto(f"https://{site}/wp-admin/plugin-editor.php?plugin={up.quote(main, safe='')}", wait_until="domcontentloaded", timeout=60000); time.sleep(3)
            tree = pg.evaluate("() => [...document.querySelectorAll('#plugin-files a, ul.tree a, .plugin-editor-tree a')].map(a => a.getAttribute('href'))")
            files = []
            for h in tree or []:
                q = up.parse_qs(up.urlparse(h).query); f = q.get("file", [None])[0]
                if f and f not in files: files.append(f)
            if not files: files = [main]
            out.setdefault("tree", {})[main] = files
            for f in files[:40]:
                if not f.endswith((".php", ".txt", ".js", ".json", ".md")): continue
                pg.goto(f"https://{site}/wp-admin/plugin-editor.php?plugin={up.quote(main, safe='')}&file={up.quote(f, safe='')}", wait_until="domcontentloaded", timeout=60000); time.sleep(2)
                txt = pg.evaluate("() => { const t = document.querySelector('#newcontent'); return t ? t.value : null; }")
                if txt is None:
                    out["errors"].append(f"{f}: no editor textarea (DISALLOW_FILE_EDIT?) title={pg.title()}")
                else:
                    out["files"][f] = txt
        except Exception as e:
            out["errors"].append(f"{main}: {type(e).__name__}: {str(e)[:150]}")
    br.close()
json.dump(out, open("/out/editor.json", "w"), indent=1)
print("done", len(out["files"]), "files", len(out["errors"]), "errors")
