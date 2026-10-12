# syntax=docker/dockerfile:1
# Override with a qualified tag@sha256:digest for a frozen release.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE} AS build
WORKDIR /build/project
COPY pyproject.toml ./
COPY src ./src
COPY packaging/core/requirements.txt packaging/core/prepare_source.py ./packaging/core/
RUN python packaging/core/prepare_source.py --repository /build/project --destination /build/core
RUN python -m pip wheel --no-cache-dir --wheel-dir /build/wheels /build/core

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/orionq
RUN groupadd --gid 10001 orionq && useradd --uid 10001 --gid 10001 --no-create-home orionq
COPY --from=build /build/wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels quantum-twin \
    && python -m pip check \
    && rm -rf /wheels
COPY --from=build /build/core/core-artifact.json /opt/orionq/core-artifact.json
COPY docs/thesis/tcc_claim_registry.json ./docs/thesis/tcc_claim_registry.json
COPY evidence/tcc/v1.0.0/tables ./evidence/tcc/v1.0.0/tables
COPY configs/qkd_assurance/irelandqci_example_operating_point.json ./configs/qkd_assurance/
COPY src/quantum_autopilot/qkd/data/ireland_qci_grid_v1.json ./src/quantum_autopilot/qkd/data/
COPY outputs/qkd_coexistence_measured/experimental_cells.csv ./outputs/qkd_coexistence_measured/
COPY packaging/core/healthcheck.py ./healthcheck.py
RUN mkdir -p /var/lib/orionq /etc/orionq /run/secrets \
    && chown 10001:10001 /var/lib/orionq \
    && chmod 700 /var/lib/orionq \
    && python -c "import importlib.util; assert importlib.util.find_spec('PySide6') is None; assert importlib.util.find_spec('torch') is None; assert importlib.util.find_spec('quantum_autopilot.console') is None"
USER 10001:10001
EXPOSE 8765
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "/opt/orionq/healthcheck.py", "--port", "8765"]
CMD ["python", "-m", "quantum_autopilot.service.cli", "--config", "/etc/orionq/core.json", "serve"]
