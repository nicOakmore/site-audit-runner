FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
RUN pip install --no-cache-dir requests beautifulsoup4
WORKDIR /app
COPY site_audit.py site_audit_browser.py start.sh /app/
RUN chmod +x /app/start.sh && mkdir -p /out
CMD ["/app/start.sh"]
