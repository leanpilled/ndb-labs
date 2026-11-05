#!/usr/bin/env bash
# Generates a self-signed root CA plus a shared node certificate (with SANs for
# every cluster hostname) and an admin certificate for the OpenSearch security plugin.
set -euo pipefail

CERT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/certs"
mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

SUBJ_BASE="/O=lab2/OU=lab2/L=Moscow/C=RU"
DAYS=3650

echo "==> Root CA"
openssl genrsa -out root-ca-key.pem 2048
openssl req -new -x509 -sha256 -days $DAYS -key root-ca-key.pem \
  -subj "${SUBJ_BASE}/CN=lab2-root-ca" -out root-ca.pem

echo "==> Node certificate (shared by all OpenSearch nodes, SAN covers all hostnames)"
openssl genrsa -out node-key-temp.pem 2048
openssl pkcs8 -inform PEM -outform PEM -in node-key-temp.pem -topk8 -nocrypt -v1 PBE-SHA1-3DES -out node-key.pem
rm -f node-key-temp.pem
openssl req -new -key node-key.pem -subj "${SUBJ_BASE}/CN=node1" -out node.csr

cat > node.ext <<'EOF'
subjectAltName = DNS:os-manager,DNS:os-hot1,DNS:os-hot2,DNS:os-warm1,DNS:opensearch-dashboards,DNS:localhost,IP:127.0.0.1
EOF

openssl x509 -req -in node.csr -CA root-ca.pem -CAkey root-ca-key.pem -CAcreateserial \
  -sha256 -days $DAYS -extfile node.ext -out node.pem

echo "==> Admin certificate (used as TLS admin identity for the security plugin)"
openssl genrsa -out admin-key-temp.pem 2048
openssl pkcs8 -inform PEM -outform PEM -in admin-key-temp.pem -topk8 -nocrypt -v1 PBE-SHA1-3DES -out admin-key.pem
rm -f admin-key-temp.pem
openssl req -new -key admin-key.pem -subj "${SUBJ_BASE}/CN=admin" -out admin.csr
openssl x509 -req -in admin.csr -CA root-ca.pem -CAkey root-ca-key.pem -CAcreateserial \
  -sha256 -days $DAYS -out admin.pem

rm -f node.csr admin.csr node.ext root-ca.srl
chmod 644 *.pem

echo "==> Done. Files in $CERT_DIR:"
ls -1 "$CERT_DIR"
