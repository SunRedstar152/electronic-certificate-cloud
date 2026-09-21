FROM postgres:18-alpine

RUN apk add --no-cache python3 tzdata
WORKDIR /backup-tools
COPY scripts/backup.py /backup-tools/backup.py
ENV PYTHONUNBUFFERED=1 PGCONNECT_TIMEOUT=10
ENTRYPOINT ["python3", "/backup-tools/backup.py"]
CMD ["daemon"]
