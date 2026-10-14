#!/usr/bin/env bash
set -Eeuo pipefail
source .github/security-tools.env
mkdir -p "$RUNNER_TEMP/reports" "$RUNNER_TEMP/trivy-cache"
mounts=(-v "$PWD:/src:ro" -v "$RUNNER_TEMP/reports:/reports")
harden=(--rm --cap-drop=ALL --security-opt=no-new-privileges --user "$(id -u):$(id -g)" -e HOME=/tmp)
case "${1:-}" in
  gitleaks)
    docker run "${harden[@]}" "${mounts[@]}" --entrypoint gitleaks "$GITLEAKS_IMAGE" \
      git /src --gitleaks-ignore-path /src/.gitleaksignore --redact=100 --report-format json --report-path /reports/gitleaks.json
    ;;
  semgrep)
    docker run "${harden[@]}" "${mounts[@]}" --workdir /src --entrypoint semgrep "$SEMGREP_IMAGE" \
      scan --config .github/semgrep.yml --error --severity ERROR --metrics off \
      --disable-version-check --json --output /reports/semgrep.json .
    ;;
  source)
    docker run "${harden[@]}" "${mounts[@]}" -v "$RUNNER_TEMP/trivy-cache:/cache" --entrypoint trivy "$TRIVY_IMAGE" \
      fs --cache-dir /cache --scanners vuln,misconfig,secret --include-dev-deps --severity HIGH,CRITICAL --exit-code 1 \
      --skip-dirs .git --skip-dirs .venv --skip-dirs node_modules --skip-dirs .tools --skip-dirs .cache \
      --format json --output /reports/trivy-source.json /src
    ;;
  image|sbom)
    : "${IMAGE_REF:?IMAGE_REF is required}" "${TRIVY_PASSWORD:?Registry token is required}"
    args=(image --cache-dir /cache --scanners vuln --severity HIGH,CRITICAL)
    if [[ "$1" == image ]]; then
      args+=(--exit-code 1 --format json --output /reports/trivy-image.json)
    else
      args+=(--exit-code 0 --format cyclonedx --output /reports/image.cdx.json)
    fi
    docker run "${harden[@]}" -v "$RUNNER_TEMP/reports:/reports" -v "$RUNNER_TEMP/trivy-cache:/cache" \
      -e TRIVY_USERNAME="$GITHUB_ACTOR" -e TRIVY_PASSWORD --entrypoint trivy "$TRIVY_IMAGE" \
      "${args[@]}" "$IMAGE_REF"
    ;;
  *) printf '%s\n' 'Expected gitleaks, semgrep, source, image or sbom.' >&2; exit 2 ;;
esac
