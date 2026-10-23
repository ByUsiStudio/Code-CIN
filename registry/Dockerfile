# package.codecin.org.cn registry server
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/pic-registry .

FROM alpine:3.20
RUN adduser -D -H -u 10001 pic && mkdir -p /data && chown pic:pic /data
COPY --from=build /out/pic-registry /usr/local/bin/pic-registry
USER pic
ENV PIC_SERVER_ADDR=:8080 \
    PIC_SERVER_DATA=/data \
    PIC_PUBLIC_URL=https://package.codecin.org.cn \
    CDIFIT_API_BASE=https://www.cdifit.cn
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/pic-registry"]
