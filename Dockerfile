FROM python:3.12-slim

LABEL maintainer="nesquena"
LABEL description="Hermes Web UI — browser interface for Hermes Agent"

# Install system packages
ENV DEBIAN_FRONTEND=noninteractive
ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG APT_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG UV_VERSION=0.11.16

# Make use of apt-cacher-ng if available
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
          -e "s|http://deb.debian.org/debian|${APT_MIRROR}|g" \
          -e "s|http://security.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
          -e "s|http://deb.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
          /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i \
          -e "s|http://deb.debian.org/debian|${APT_MIRROR}|g" \
          -e "s|http://security.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
          -e "s|http://deb.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
          /etc/apt/sources.list; \
    fi; \
    if [ "A${BUILD_APT_PROXY:-}" != "A" ]; then \
        echo "Using APT proxy: ${BUILD_APT_PROXY}"; \
        printf 'Acquire::http::Proxy "%s";\n' "$BUILD_APT_PROXY" > /etc/apt/apt.conf.d/01proxy; \
    fi; \
    apt-get update -y --fix-missing \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      gnupg \
      locales \
      openssh-client \
      rsync \
      sudo \
      wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# UTF-8
RUN localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8
ENV LANG=en_US.utf8
ENV LC_ALL=C

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /apptoo

# Every sudo group user does not need a password
RUN echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

# Create a new group for the hermeswebui and hermeswebuitoo users
RUN groupadd -g 1024 hermeswebui \ 
    && groupadd -g 1025 hermeswebuitoo

# The hermeswebui (resp. hermeswebuitoo) user will have UID 1024 (resp. 1025), 
# be part of the hermeswebui (resp. hermeswebuitoo) and users groups and be sudo capable (passwordless) 
RUN useradd -u 1024 -d /home/hermeswebui -g hermeswebui -s /bin/bash -m hermeswebui \
    && usermod -G users hermeswebui \
    && adduser hermeswebui sudo
RUN useradd -u 1025 -d /home/hermeswebuitoo -g hermeswebuitoo -s /bin/bash -m hermeswebuitoo \
    && usermod -G users hermeswebuitoo \
    && adduser hermeswebuitoo sudo
RUN chown -R hermeswebuitoo:hermeswebuitoo /apptoo

USER root

COPY --chmod=555 docker_init.bash /hermeswebui_init.bash

RUN touch /.within_container

# Remove APT proxy configuration and clean up APT downloaded files
RUN rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/01proxy \
    && apt-get clean

USER root

# Pre-install uv system-wide so the container doesn't need internet access at runtime.
# Install a fixed version from the configured PyPI mirror instead of fetching
# the remote installer script from astral.sh during every uncached build.
RUN python -m pip install --no-cache-dir -i "${PYPI_INDEX_URL}" "uv==${UV_VERSION}"

USER hermeswebuitoo

COPY --chown=hermeswebuitoo:hermeswebuitoo . /apptoo

# Bake the git version tag into the image so the settings badge works even
# when .git is not present (it is excluded by .dockerignore).
# CI passes: --build-arg HERMES_VERSION=$(git describe --tags --always)
# Local builds that omit the arg get "unknown" as the fallback.
ARG HERMES_VERSION=unknown
RUN echo "__version__ = '${HERMES_VERSION}'" > /apptoo/api/_version.py

# Default to binding all interfaces (required for container networking)
ENV HERMES_WEBUI_HOST=0.0.0.0
ENV HERMES_WEBUI_PORT=8787

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8787/health || exit 1

CMD ["/hermeswebui_init.bash"]

