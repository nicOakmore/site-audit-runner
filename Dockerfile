FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
RUN python3 -m pip install --no-cache-dir --break-system-packages requests beautifulsoup4 playwright==1.49.1 \
 && python3 -m playwright install chromium \
 && python3 -c "import playwright, requests, bs4; print('deps ok')"
WORKDIR /app
COPY site_audit.py site_audit_browser.py php_check_env.py plugins_check.py start.sh /app/
RUN chmod +x /app/start.sh && mkdir -p /out
CMD ["/app/start.sh"]
