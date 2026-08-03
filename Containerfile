FROM registry.access.redhat.com/ubi9/python-312:latest

USER 0

RUN dnf install -y jq tar gzip && dnf clean all

RUN pip install --no-cache-dir locust==2.32.4 elasticsearch pyyaml

RUN curl -sL https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux.tar.gz \
    | tar xz -C /usr/local/bin/ oc kubectl

RUN curl -sL https://github.com/kube-burner/kube-burner-ocp/releases/download/v1.3/kube-burner-ocp-V1.3-linux-x86_64.tar.gz \
    | tar xz -C /usr/local/bin/ kube-burner-ocp \
    && ln -s /usr/local/bin/kube-burner-ocp /usr/local/bin/kube-burner

WORKDIR /opt/lcs-load-generator

COPY locust/ ./locust/
COPY py_commons/ ./py_commons/
COPY lcs-load-generator ./
RUN chmod +x lcs-load-generator

USER 1001

ENTRYPOINT ["python3", "./lcs-load-generator", "run"]
