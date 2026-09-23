FROM python:3.10-slim
WORKDIR /app
COPY poker_lan.py .
EXPOSE 5051
CMD ["python3", "poker_lan.py", "--web", "--web-port", "5051"]
