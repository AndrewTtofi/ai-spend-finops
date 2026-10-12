FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# Dependencies first, from the hashed lockfile, so this layer is cached between builds.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY pyproject.toml ./
COPY finops ./finops
COPY config ./config
RUN pip install --no-cache-dir --no-deps .

# Not root: nothing here needs it. The local store lives on a volume.
RUN useradd --create-home --uid 1000 finops && mkdir -p /srv/data && chown finops /srv/data
USER finops

ENTRYPOINT ["finops"]
CMD ["demo"]
