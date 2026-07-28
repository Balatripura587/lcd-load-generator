FROM registry.access.redhat.com/ubi9/python-312:latest

USER 0

RUN dnf install -y jq tar gzip && dnf clean all

RUN pip install --no-cache-dir locust==2.32.4 elasticsearch pyyaml

RUN curl -sL https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux.tar.gz \
    | tar xz -C /usr/local/bin/ oc kubectl

WORKDIR /opt/lcs-load-generator

COPY locust/ ./locust/
COPY lcs-load-generator cluster_metadata.py ./
RUN chmod +x lcs-load-generator

USER 1001

ENTRYPOINT ["python3", "./lcs-load-generator", "run"]
