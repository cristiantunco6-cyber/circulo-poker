FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=10000
WORKDIR /app
COPY poker_lan.py /app/poker_lan.py
USER 65534:65534
EXPOSE 10000
CMD ["python", "poker_lan.py", "--server", "--online"]
