#!/usr/bin/env bash
# scripts/backup_crypto.sh — portable archive encryption helpers
# Source this file; do not execute it directly.

med_backup_encrypt_stream() {
    local output_path="$1"

    if [[ -n "${MED_BACKUP_PASSPHRASE:-}" ]]; then
        require_command openssl
        gzip -c | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:MED_BACKUP_PASSPHRASE -out "${output_path}"
        return 0
    fi

    require_command age
    if [[ ! -t 0 && ! -t 2 ]]; then
        die "Encryption requires an interactive terminal or MED_BACKUP_PASSPHRASE."
    fi
    gzip -c | age -e -p -o "${output_path}" -
}

med_backup_decrypt_stream() {
    local input_path="$1"

    if med_backup_is_openssl_encrypted "${input_path}"; then
        require_command openssl
        if [[ -n "${PASSPHRASE_FILE:-}" ]]; then
            openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:${PASSPHRASE_FILE}" -in "${input_path}" | gzip -dc
        elif [[ -n "${MED_BACKUP_PASSPHRASE:-}" ]]; then
            openssl enc -d -aes-256-cbc -pbkdf2 -pass env:MED_BACKUP_PASSPHRASE -in "${input_path}" | gzip -dc
        else
            openssl enc -d -aes-256-cbc -pbkdf2 -pass stdin -in "${input_path}" | gzip -dc
        fi
        return 0
    fi

    require_command age
    if [[ -n "${PASSPHRASE_FILE:-}" ]]; then
        die "--passphrase-file is not supported for age-encrypted archives; use MED_BACKUP_PASSPHRASE or decrypt interactively."
    fi
    if [[ -n "${MED_BACKUP_PASSPHRASE:-}" ]]; then
        die "MED_BACKUP_PASSPHRASE cannot decrypt age-encrypted archives; enter the passphrase interactively."
    fi
    age -d -o - "${input_path}" | gzip -dc
}

med_backup_is_openssl_encrypted() {
    local input_path="$1"
    local magic=""

    magic="$(head -c 8 "${input_path}" 2>/dev/null || true)"
    [[ "${magic}" == "Salted__" ]]
}

med_backup_is_encrypted_path() {
    local path="$1"
    case "${path}" in
        *.age|*.enc) return 0 ;;
    esac
    return 1
}
