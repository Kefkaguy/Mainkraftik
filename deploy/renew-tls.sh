#!/bin/sh
set -eu
docker run --rm --name intelligence-cert-renew --memory 192m --cpus 0.5 \
  -v /opt/keftrade/deploy/production/certbot/conf:/etc/letsencrypt \
  -v /opt/keftrade/deploy/production/certbot/www:/var/www/certbot \
  certbot/certbot@sha256:0e40d51116b9e1058466aa2fd8f342b1d676dfbc17e47877a9e02c3e2460580a \
  renew --cert-name keftrade.duckdns.org --webroot -w /var/www/certbot \
  --non-interactive --no-random-sleep-on-renew
docker exec production-nginx-1 nginx -t
docker exec production-nginx-1 nginx -s reload
