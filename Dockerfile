FROM python:3.12-alpine

WORKDIR /app
COPY icy_proxy.py .

EXPOSE 8080
ENTRYPOINT ["python", "-u", "icy_proxy.py"]
