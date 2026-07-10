#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/docs/diagrams"

K8S_DIR="${ROOT_DIR}/k8s_mani"

BACKEND_MANIFESTS=(
  "${K8S_DIR}/router-app_cm.yml"
  "${K8S_DIR}/router-app_dep.yml"
  "${K8S_DIR}/router-app_svc.yml"
  "${K8S_DIR}/dashboard_ingress.yml"
  "${K8S_DIR}/optional/router-dashboard-hpa.yml"
)

DATABASE_MANIFESTS=(
  "${K8S_DIR}/manual_sc.yml"
  "${K8S_DIR}/time_db/timescale_pv.yml"
  "${K8S_DIR}/time_db/timescaledb_pvc.yml"
)

TRAEFIK_MANIFESTS=(
  "${K8S_DIR}/traefik.yml"
)

MONITORING_MANIFESTS=(
  "${K8S_DIR}/monitoring.yml"
  "${K8S_DIR}/monitoring.yaml"
  "${K8S_DIR}/monitoring"
)

require_command() {
  local command_name="$1"
  local install_hint="$2"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'ERROR: required command "%s" was not found.\n' "${command_name}" >&2
    printf '%s\n' "${install_hint}" >&2
    exit 1
  fi
}

existing_files() {
  local path
  for path in "$@"; do
    if [[ -f "${path}" ]]; then
      printf '%s\n' "${path}"
    fi
  done
}

generate_diagram() {
  local name="$1"
  shift

  local -a manifests=("$@")
  if [[ "${#manifests[@]}" -eq 0 ]]; then
    printf 'Skipping %s: no manifests found.\n' "${name}"
    return 0
  fi

  local svg_output="${OUT_DIR}/${name}.svg"
  kube-diagrams -f svg -o "${svg_output}" "${manifests[@]}"
  printf 'Generated %s\n' "${svg_output}"

  local mermaid_output="${OUT_DIR}/${name}.mmd"
  if kube-diagrams -f mermaid -o "${mermaid_output}" "${manifests[@]}" >/tmp/kube-diagrams-${name}-mermaid.log 2>&1; then
    printf 'Generated %s\n' "${mermaid_output}"
  else
    rm -f "${mermaid_output}"
    printf 'Skipping optional Mermaid output for %s; this KubeDiagrams installation may not support it.\n' "${name}" >&2
  fi
}

main() {
  require_command "kube-diagrams" "Install it with: python3 -m pip install --user KubeDiagrams"
  require_command "dot" "Install Graphviz first, for example: sudo dnf install graphviz"

  mkdir -p "${OUT_DIR}"

  mapfile -t backend_files < <(existing_files "${BACKEND_MANIFESTS[@]}")
  mapfile -t database_files < <(existing_files "${DATABASE_MANIFESTS[@]}")
  mapfile -t traefik_files < <(existing_files "${TRAEFIK_MANIFESTS[@]}")
  mapfile -t monitoring_files < <(existing_files "${MONITORING_MANIFESTS[@]}")

  if [[ "${#backend_files[@]}" -eq 0 ]]; then
    printf 'ERROR: no backend Kubernetes manifests found under %s.\n' "${K8S_DIR}" >&2
    exit 1
  fi

  local -a full_cluster_files=(
    "${traefik_files[@]}"
    "${backend_files[@]}"
    "${database_files[@]}"
    "${monitoring_files[@]}"
  )

  generate_diagram "backend" "${backend_files[@]}"
  generate_diagram "full-cluster" "${full_cluster_files[@]}"
  generate_diagram "database" "${database_files[@]}"
  generate_diagram "traefik" "${traefik_files[@]}"
  generate_diagram "monitoring" "${monitoring_files[@]}"

  printf '\nDiagram generation complete. Outputs are in %s\n' "${OUT_DIR}"
}

main "$@"
