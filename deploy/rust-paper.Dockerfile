# Build only from a reviewed immutable copy of the existing application image.
# The new container runs one native process; no Python trading worker is started.
ARG RUNTIME_IMAGE
FROM ${RUNTIME_IMAGE}
USER root
COPY --chmod=0555 release/mainkraft /usr/local/bin/mainkraft
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/mainkraft"]
CMD ["--help"]
