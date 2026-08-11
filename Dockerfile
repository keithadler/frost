# frost in a container, for a pipeline that wants `--check --strict` without a
# Python setup step.
#
# It reviews scripts. It does not run them well: a container has none of the
# tools a real script calls, so `frost script.frost` inside here will fail at
# the first `run` for want of git or curl. That is the right shape. Reviewing
# is the part you want in a pipeline, and running somebody's script inside an
# image built for reviewing it is how a review tool becomes an execution
# surface.
# To build it yourself, make the wheel first, because the image is built from
# the wheel and not from the source tree:
#
#   python -m build --outdir dist
#   docker build --build-arg FROST_VERSION=0.9.3 -t frost .
FROM python:3.12-slim

# From the wheel this release already built and published, copied into the
# context, rather than fetched back from PyPI.
#
# Fetching it back looked more honest and was a race. PyPI's index is
# eventually consistent across edges: the runner's pip reported 0.9.2
# available and sixteen seconds later the build container, talking to a
# different edge, could not find it. Waiting longer on the runner cannot fix
# that, because the runner was never the one that had to see it.
#
# This is also the stronger guarantee. The image holds the exact file that was
# uploaded, not a copy that resembles it.
ARG FROST_VERSION
COPY dist/ /tmp/dist/
# Refused rather than defaulted. An empty build-arg would install whatever the
# glob happened to match, and the image would carry a tag naming a version it
# does not hold.
RUN test -n "${FROST_VERSION}" || (echo "FROST_VERSION build-arg is required" \
      && exit 1) \
    && pip install --no-cache-dir \
        "/tmp/dist/frostlang-${FROST_VERSION}-py3-none-any.whl[keystore]" \
    && rm -rf /tmp/dist \
    && frost --version

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
