# frost in a container, for a pipeline that wants `--check --strict` without a
# Python setup step.
#
# It reviews scripts. It does not run them well: a container has none of the
# tools a real script calls, so `frost script.frost` inside here will fail at
# the first `run` for want of git or curl. That is the right shape. Reviewing
# is the part you want in a pipeline, and running somebody's script inside an
# image built for reviewing it is how a review tool becomes an execution
# surface.
FROM python:3.12-slim

# From PyPI rather than the build context, so the image contains a released
# version somebody can name, not whatever was in the working tree.
ARG FROST_VERSION
# Refused rather than defaulted. An empty build-arg would install whatever is
# newest, and the image would carry a tag naming a version it does not hold.
RUN test -n "${FROST_VERSION}" || (echo "FROST_VERSION build-arg is required" \
      && exit 1) \
    && pip install --no-cache-dir "frostlang[keystore]==${FROST_VERSION}"

# Nothing here needs root, and a reviewer that runs as root is a reviewer that
# can write to whatever gets mounted into it.
RUN useradd --create-home --uid 1000 frost
USER frost
WORKDIR /work

# --check rather than a bare frost, so `docker run ... script.frost` reviews
# by default and running takes a deliberate override.
ENTRYPOINT ["frost"]
CMD ["--help"]

LABEL org.opencontainers.image.title="frost" \
      org.opencontainers.image.description="Review shell scripts before they run" \
      org.opencontainers.image.source="https://github.com/keithadler/frost" \
      org.opencontainers.image.licenses="MIT"
