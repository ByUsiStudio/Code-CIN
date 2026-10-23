# package.codecin.org.cn registry server（Python 标准库 HTTP + rich）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py store.py auth.py version.py ./
RUN groupadd --gid 10001 pic \
    && useradd --uid 10001 --gid 10001 --no-create-home \
       --shell /usr/sbin/nologin pic \
    && mkdir -p /data && chown pic:pic /data

USER pic
ENV PIC_SERVER_ADDR=:18080 \
    PIC_SERVER_DATA=/data \
    PIC_PUBLIC_URL=https://package.codecin.org.cn \
    CDIFIT_API_BASE=https://www.cdifit.cn
VOLUME ["/data"]
EXPOSE 18080
ENTRYPOINT ["python", "/app/server.py"]
