FROM registry.access.redhat.com/ubi9/python-312:latest

USER 0

RUN dnf install -y jq tar gzip && dnf clean all

# kube-burner v1.10.4  — used for Prometheus metrics scraping
RUN curl -sL https://github.com/kube-burner/kube-burner/releases/download/v1.10.4/kube-burner-V1.10.4-linux-x86_64.tar.gz \
    | tar xz -C /usr/local/bin/ kube-burner

WORKDIR /opt/lcs-load-generator

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir "rh-py-commons[ocp_metadata,indexers] @ git+https://github.com/cloud-bulldozer/py-commons.git"

COPY locust/ ./locust/
COPY lcs-load-generator ./
RUN chmod +x lcs-load-generator

USER 1001

ENTRYPOINT ["python3", "./lcs-load-generator"]
CMD ["run"]
