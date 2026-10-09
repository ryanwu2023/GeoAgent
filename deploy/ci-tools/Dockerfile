# Build with the verified linux/amd64 kubectl v1.28.15 binary in the context.
FROM scratch
COPY --chmod=0555 kubectl /kubectl
USER 65532:65532
ENTRYPOINT ["/kubectl"]
