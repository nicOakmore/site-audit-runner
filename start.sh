#!/bin/bash
cd /out && python3 -m http.server ${PORT:-10000} --directory /out >/out/http.log 2>&1 &
echo "started $(date -u) mode=${MODE:-crawl}" > /out/STATUS
if [ "${MODE}" = "php" ]; then
  python3 /app/php_check_env.py > /out/php_check.log 2>&1
elif [ "${MODE}" = "editor" ]; then
  python3 /app/editor_read.py > /out/editor.log 2>&1
elif [ "${MODE}" = "fetchplugins" ]; then
  python3 /app/fetch_plugins.py > /out/fetch.log 2>&1
elif [ "${MODE}" = "plugins" ]; then
  python3 /app/plugins_check.py > /out/plugins.log 2>&1
else
  TARGET=${TARGET:-https://cumula3.com}
  python3 /app/site_audit_browser.py "$TARGET" --out /out/audit.json --max-pages 300 --delay ${DELAY:-1.0} ${CLICK_FLAG} > /out/crawl.log 2>&1
fi
echo "exit $? $(date -u)" >> /out/STATUS
echo done > /out/DONE
wait
