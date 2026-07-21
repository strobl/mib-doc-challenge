FROM python:3.12.11-slim-bookworm

ARG APP_UID=10001
ARG APP_GID=10001

# Keep every common native/Python thread pool inside the four-vCPU scoring limit.
# Python bytecode and user caches are disabled because the container root is
# read-only at runtime. Any future scratch data belongs under /tmp.
ENV BLIS_NUM_THREADS=4 \
    HOME=/tmp \
    MALLOC_ARENA_MAX=4 \
    MIB_MAX_WORKERS=4 \
    MKL_NUM_THREADS=4 \
    NUMEXPR_NUM_THREADS=4 \
    OMP_NUM_THREADS=4 \
    OPENBLAS_NUM_THREADS=4 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TMPDIR=/tmp \
    TOKENIZERS_PARALLELISM=false \
    VECLIB_MAXIMUM_THREADS=4

WORKDIR /app

COPY requirements.lock /app/requirements.lock
RUN python3 -m pip install \
      --disable-pip-version-check \
      --no-cache-dir \
      --require-hashes \
      --requirement /app/requirements.lock \
    && groupadd --gid "${APP_GID}" mib \
    && useradd \
      --uid "${APP_UID}" \
      --gid "${APP_GID}" \
      --home-dir /tmp \
      --no-create-home \
      --shell /usr/sbin/nologin \
      mib

COPY run.sh solution.py /app/
RUN chmod 0555 /app/run.sh /app/solution.py \
    && chmod 0444 /app/requirements.lock

USER mib:mib

ENTRYPOINT ["/app/run.sh"]
