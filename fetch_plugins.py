"""Read-only: download named plugin folders over FTPS into /out/plugins/<name>/ and list wp-content/plugins."""
import ftplib, io, os, json
host, user, pw = os.environ["FTP_HOST"], os.environ["HOST_USER"], os.environ["HOST_PASS"]
names = os.environ.get("PLUGIN_DIRS", "").split(",")
f = ftplib.FTP_TLS(timeout=60); f.connect(host, 21); f.auth(); f.login(user, pw); f.prot_p()
lst = []; f.retrlines("LIST wp-content/plugins", lst.append); open("/out/plugins_dir.txt", "w").write("\n".join(lst))
def walk(remote, local):
    os.makedirs(local, exist_ok=True)
    entries = []; f.retrlines("LIST " + remote, entries.append)
    for e in entries:
        parts = e.split(None, 8)
        if len(parts) < 9: continue
        name = parts[8]
        if name in (".", ".."): continue
        if e.startswith("d"): walk(remote + "/" + name, local + "/" + name)
        else:
            if name.endswith((".php", ".txt", ".json", ".md", ".css", ".js")) and int(parts[4]) < 2_000_000:
                with open(local + "/" + name, "wb") as fh: f.retrbinary("RETR " + remote + "/" + name, fh.write)
for n in names:
    if n: walk("wp-content/plugins/" + n, "/out/plugins/" + n)
f.quit()
import tarfile
with tarfile.open("/out/plugins.tar.gz", "w:gz") as t: t.add("/out/plugins", arcname="plugins")
print("done", names)
