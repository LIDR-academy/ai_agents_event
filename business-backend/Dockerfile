# syntax=docker/dockerfile:1
# check=error=true

# =============================================================================
# Business backend (Rails 8) — Session 15
# =============================================================================
# Until S14 this image carried ONLY the gems: the application source arrived
# through a compose bind mount, so the image was useless anywhere but this
# laptop. Session 15 makes it SELF-CONTAINED — the source is baked in and
# `docker run` alone boots a working app, which is the precondition for the
# cloud deploy in the live session.
#
# Deliberate scope limit: the container still runs with RAILS_ENV=development
# by default. Going to production here is NOT a one-line change — Rails 8 ships
# a multi-database `production:` block in config/database.yml (primary + cache +
# queue + cable, i.e. solid_cache/solid_queue/solid_cable), so it needs four
# databases, their migrations and a SECRET_KEY_BASE. That belongs to the cloud
# session, not to this pre-exercise. See docs/deployment-local.md.

ARG RUBY_VERSION=3.4.4
FROM docker.io/library/ruby:$RUBY_VERSION-slim

WORKDIR /rails

# Toolchain + runtime libraries (single layer, no recommends).
# - build-essential / git / pkg-config / libyaml-dev: native gem compilation
#   (bootsnap, debug, nokogiri).
# - libpq-dev / postgresql-client: pg driver + psql/pg_isready for debugging.
# - libvips / libjemalloc2: parity with what Rails-generated prod image carried.
# - curl: HEALTHCHECK and ad-hoc debugging.
RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y \
        build-essential \
        curl \
        git \
        libjemalloc2 \
        libpq-dev \
        libvips \
        libyaml-dev \
        pkg-config \
        postgresql-client && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

ENV BUNDLE_PATH=/usr/local/bundle \
    BUNDLE_JOBS=4 \
    BUNDLE_RETRY=3 \
    RAILS_ENV=development \
    RAILS_LOG_TO_STDOUT=1

# Gems first, in their own layer: they only reinstall when the Gemfile changes,
# so editing application code does not pay for a bundle install.
COPY Gemfile Gemfile.lock ./
RUN bundle install

# foreman is intentionally outside bundle (bin/dev convention).
RUN gem install foreman

# Application source. From here on, any code change invalidates the cache —
# which is why it comes AFTER the gem install.
COPY . .

# Build the Tailwind stylesheet at image-build time so the container does not
# depend on the `tailwindcss:watch` process to render correctly. In dev the
# override re-enables the watcher via bin/dev.
# SECRET_KEY_BASE_DUMMY lets the asset task boot the app without a real secret.
RUN SECRET_KEY_BASE_DUMMY=1 bundle exec rails tailwindcss:build

# Run as a non-root user (parity with the ai-service image). Ownership covers
# the writable paths Rails needs at runtime: logs, tmp (pids, cache) and storage.
RUN groupadd --system --gid 1000 rails && \
    useradd --system --uid 1000 --gid 1000 --create-home --shell /bin/bash rails && \
    mkdir -p log tmp storage && \
    chown -R rails:rails /rails log tmp storage
USER 1000:1000

ENTRYPOINT ["/rails/bin/docker-entrypoint"]

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:3000/up || exit 1

CMD ["bin/rails", "server", "-b", "0.0.0.0", "-p", "3000"]
