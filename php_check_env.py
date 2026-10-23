"""Read-only hosting check: cPanel UAPI PHP version per vhost, then .htaccess/.user.ini/php.ini over FTPS."""
import ftplib, io, json, os, re, sys, requests
host, user, pw = os.environ["FTP_HOST"], os.environ["HOST_USER"], os.environ["HOST_PASS"]
site = os.environ.get("SITE", "cumula3.com")
out = {}
# cPanel UAPI over basic auth, port 2083
for ep in ("LangPHP/php_get_vhost_versions", "LangPHP/php_get_system_default_version", "LangPHP/php_get_installed_versions", "DomainInfo/list_domains"):
    try:
        r = requests.get(f"https://{site}:2083/execute/{ep}", auth=(user, pw), timeout=40, verify=False)
        out[ep] = {"status": r.status_code, "body": r.text[:4000]}
    except Exception as e:
        out[ep] = {"error": f"{type(e).__name__}: {e}"}
# FTPS
try:
    f = ftplib.FTP_TLS(timeout=40); f.connect(host, 21); f.auth(); f.login(user, pw); f.prot_p()
    names = []; f.retrlines("LIST", names.append); out["ftp_root"] = names[:80]
    files = {}
    for d in ["", "public_html", "www", site, "domains/" + site]:
        for fn in (".htaccess", ".user.ini", "php.ini"):
            p = (d + "/" if d else "") + fn
            buf = io.BytesIO()
            try:
                f.retrbinary("RETR " + p, buf.write); files[p] = buf.getvalue().decode("utf-8", "replace")
            except Exception:
                pass
    try:
        sub = []; f.retrlines("LIST public_html", sub.append); out["public_html"] = sub[:80]
    except Exception as e:
        out["public_html"] = str(e)
    f.quit()
    out["files"] = files
    out["handler_lines"] = {p: [l for l in t.splitlines() if re.search(r"AddHandler|SetHandler|php8|ea-php|FcgidWrapper|AddType.*php|cPanel-generated|MultiPHP", l, re.I)] for p, t in files.items()}
except Exception as e:
    out["ftp_error"] = f"{type(e).__name__}: {e}"
json.dump(out, open("/out/php_check.json", "w"), indent=1)
print("done")
