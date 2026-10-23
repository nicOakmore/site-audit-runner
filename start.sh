#!/bin/bash
# Serve /out immediately so the platform sees an open port, then crawl.
cd /out && python3 -m http.server ${PORT:-10000} --directory /out >/out/http.log 2>&1 &
TARGET=${TARGET:-https://cumula3.com}
echo "crawl of $TARGET started $(date -u)" > /out/STATUS
python3 /app/site_audit_browser.py "$TARGET" --out /out/audit.json --max-pages 300 --delay ${DELAY:-1.5} --click > /out/crawl.log 2>&1
echo "exit $? $(date -u)" >> /out/STATUS
echo done > /out/DONE
wait
