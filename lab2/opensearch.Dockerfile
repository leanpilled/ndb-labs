ARG OPENSEARCH_VERSION=2.19.6
FROM opensearchproject/opensearch:${OPENSEARCH_VERSION}

ARG OPENSEARCH_VERSION=2.19.6
RUN /usr/share/opensearch/bin/opensearch-plugin install --batch \
    "https://github.com/opensearch-project/opensearch-prometheus-exporter/releases/download/${OPENSEARCH_VERSION}.0/prometheus-exporter-${OPENSEARCH_VERSION}.0.zip"
