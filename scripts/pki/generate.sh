#!/usr/bin/env bash
# VPN-150 runtime PKI helper. Keys and certificates stay in /run/netlab.
set -Eeuo pipefail
umask 077

PKI_DIR=/run/netlab/ipsec-pki
mkdir -p -m 0700 "${PKI_DIR}"
mode=${1:?usage: generate.sh init-ca|create-request|sign-request identity}
identity=${2:?identity is required}
[[ "${identity}" =~ ^[a-z0-9.-]+$ ]] || { echo "invalid identity: ${identity}" >&2; exit 64; }
node=${identity%.netlab}

make_leaf_extensions() {
  local file=$1 name=$2
  cat >"${file}" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=DNS:${name}
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
}

case "${mode}" in
  init-ca)
    [[ "${identity}" == hq-edge-1.netlab ]] || { echo 'CA identity must be hq-edge-1.netlab' >&2; exit 64; }
    files=(ca.key.pem ca.cert.pem hq-edge-1.key.pem hq-edge-1.cert.pem)
    present=0
    for file in "${files[@]}"; do [[ -s "${PKI_DIR}/${file}" ]] && ((present+=1)); done
    if (( present == ${#files[@]} )); then
      openssl verify -CAfile "${PKI_DIR}/ca.cert.pem" "${PKI_DIR}/hq-edge-1.cert.pem" >/dev/null
      echo 'HQ lab CA and certificate already exist; preserving runtime keys'
      exit 0
    fi
    (( present == 0 )) || { echo 'partial HQ PKI state; refusing to rotate keys implicitly' >&2; exit 1; }
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:secp384r1 -out "${PKI_DIR}/ca.key.pem"
    openssl req -new -x509 -sha384 -days 3650 -key "${PKI_DIR}/ca.key.pem" \
      -subj '/O=NetLab/CN=NetLab Phase 1 Lab CA' \
      -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
      -addext 'keyUsage=critical,keyCertSign,cRLSign' \
      -addext 'subjectKeyIdentifier=hash' -out "${PKI_DIR}/ca.cert.pem"
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:secp384r1 -out "${PKI_DIR}/hq-edge-1.key.pem"
    openssl req -new -sha384 -key "${PKI_DIR}/hq-edge-1.key.pem" \
      -subj '/O=NetLab/CN=hq-edge-1.netlab' -out "${PKI_DIR}/hq-edge-1.csr.pem"
    make_leaf_extensions "${PKI_DIR}/hq-edge-1.ext" hq-edge-1.netlab
    openssl x509 -req -sha384 -days 825 -in "${PKI_DIR}/hq-edge-1.csr.pem" \
      -CA "${PKI_DIR}/ca.cert.pem" -CAkey "${PKI_DIR}/ca.key.pem" \
      -CAcreateserial -extfile "${PKI_DIR}/hq-edge-1.ext" -out "${PKI_DIR}/hq-edge-1.cert.pem"
    chmod 0600 "${PKI_DIR}"/*.key.pem "${PKI_DIR}"/*.csr.pem "${PKI_DIR}"/*.srl
    chmod 0644 "${PKI_DIR}"/*.cert.pem
    openssl verify -CAfile "${PKI_DIR}/ca.cert.pem" "${PKI_DIR}/hq-edge-1.cert.pem"
    ;;
  create-request)
    [[ "${identity}" =~ ^((hq|br1)-edge-[12]|br2-edge-1)\.netlab$ && "${identity}" != hq-edge-1.netlab ]] || {
      echo "unexpected peer identity: ${identity}" >&2; exit 64;
    }
    key="${PKI_DIR}/${node}.key.pem"
    csr="${PKI_DIR}/${node}.csr.pem"
    if [[ -s "${key}" && -s "${csr}" ]]; then
      echo 'Branch key and CSR already exist; preserving runtime key'
      exit 0
    fi
    [[ ! -e "${key}" && ! -e "${csr}" ]] || { echo 'partial branch PKI state; refusing to rotate keys implicitly' >&2; exit 1; }
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:secp384r1 -out "${key}"
    openssl req -new -sha384 -key "${key}" -subj "/O=NetLab/CN=${identity}" -out "${csr}"
    chmod 0600 "${key}" "${csr}"
    ;;
  sign-request)
    [[ "${identity}" =~ ^((hq|br1)-edge-[12]|br2-edge-1)\.netlab$ && "${identity}" != hq-edge-1.netlab ]] || {
      echo "unexpected peer identity: ${identity}" >&2; exit 64;
    }
    cert="${PKI_DIR}/${node}.cert.pem"
    if [[ -s "${cert}" ]]; then
      openssl verify -CAfile "${PKI_DIR}/ca.cert.pem" "${cert}" >/dev/null
      echo 'Branch certificate already exists; preserving runtime PKI'
      exit 0
    fi
    [[ -s "${PKI_DIR}/ca.key.pem" && -s "${PKI_DIR}/ca.cert.pem" && -s "${PKI_DIR}/${node}.csr.pem" ]] || {
      echo 'CA or branch CSR is missing' >&2; exit 1;
    }
    make_leaf_extensions "${PKI_DIR}/${identity}.ext" "${identity}"
    openssl x509 -req -sha384 -days 825 -in "${PKI_DIR}/${node}.csr.pem" \
      -CA "${PKI_DIR}/ca.cert.pem" -CAkey "${PKI_DIR}/ca.key.pem" \
      -CAserial "${PKI_DIR}/ca.cert.srl" -extfile "${PKI_DIR}/${identity}.ext" -out "${cert}"
    chmod 0644 "${cert}"
    openssl verify -CAfile "${PKI_DIR}/ca.cert.pem" "${cert}"
    ;;
  *) echo "unknown PKI operation: ${mode}" >&2; exit 64 ;;
esac
