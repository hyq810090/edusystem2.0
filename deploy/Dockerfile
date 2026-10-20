# Code update for the running system. mathbank:base already holds Python, TeX,
# LibreOffice and the fonts, so only the application code is replaced and no
# network is needed. On a machine without it, build the base once from the
# portable Dockerfile in the project root:  docker build -t mathbank:base .
FROM mathbank:base
USER root
RUN rm -rf /app/engine /app/platform_app
COPY --chown=1000:1000 engine /app/engine
COPY --chown=1000:1000 platform_app /app/platform_app
USER 1000:1000
