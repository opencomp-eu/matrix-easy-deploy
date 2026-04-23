#!/usr/bin/env python3
# scripts/apply.py — Shared config/state engine for matrix-easy-deploy

import yaml
import os
import sys
import secrets
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_FILE = PROJECT_ROOT / 'deploy.yaml'
STATE_DIR = PROJECT_ROOT / '.matrix-easy-deploy'
ENV_FILE = PROJECT_ROOT / '.env'

def extract_base_domain(fqdn):
    """Extract base domain from FQDN (e.g., matrix.example.com -> example.com)"""
    parts = fqdn.split('.')
    if len(parts) >= 3:
        return '.'.join(parts[1:])
    return fqdn

def load_config():
    """Load deploy.yaml"""
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)

def validate_config(config):
    """Basic validation of config"""
    if 'matrix' not in config:
        raise ValueError("Missing 'matrix' section in deploy.yaml")
    if 'domain' not in config['matrix']:
        raise ValueError("Missing matrix.domain in deploy.yaml")
    # Add more validations as needed

def derive_values(config):
    """Compute derived values from config"""
    derived = {}

    matrix = config['matrix']
    features = config.get('features', {})

    # SERVER_NAME default
    derived['SERVER_NAME'] = matrix.get('server_name', extract_base_domain(matrix['domain']))

    # Federation settings
    fed_enabled = features.get('federation_enabled', True)
    derived['FEDERATION_WHITELIST'] = '~' if fed_enabled else '[]'
    derived['ALLOW_PUBLIC_ROOMS_FEDERATION'] = 'true' if fed_enabled else 'false'

    # Registration
    derived['ENABLE_REGISTRATION'] = 'true' if features.get('registration_enabled', False) else 'false'

    # Element
    element = features.get('element', {})
    derived['INSTALL_ELEMENT'] = 'true' if element.get('enabled', True) else 'false'
    if derived['INSTALL_ELEMENT'] == 'true':
        default_element_domain = f"element.{extract_base_domain(matrix['domain'])}"
        derived['ELEMENT_DOMAIN'] = element.get('domain', default_element_domain)

    # Calls/LiveKit
    calls = features.get('calls', {})
    if calls.get('enabled', True):
        default_livekit_domain = f"livekit.{extract_base_domain(matrix['domain'])}"
        derived['LIVEKIT_DOMAIN'] = calls.get('livekit_domain', default_livekit_domain)

    # CADDY_MATRIX_HOSTS
    hosts = [matrix['domain']]
    if derived['SERVER_NAME'] != matrix['domain']:
        hosts.append(derived['SERVER_NAME'])
    derived['CADDY_MATRIX_HOSTS'] = ','.join(hosts)

    # SSO
    sso = features.get('sso', {})
    if sso.get('enabled', False):
        derived['ENABLE_SSO'] = 'true'
        providers = sso.get('providers', [])
        derived['OIDC_PROVIDER_COUNT'] = str(len(providers))
        derived['OIDC_PROVIDER_NAMES'] = ','.join(p.get('name', '') for p in providers)
        # TODO: build OIDC_PROVIDERS_JSON
        derived['OIDC_PROVIDERS_JSON'] = '[]'  # placeholder
    else:
        derived['ENABLE_SSO'] = 'false'
        derived['OIDC_PROVIDERS_JSON'] = '[]'
        derived['OIDC_PROVIDER_COUNT'] = '0'
        derived['OIDC_PROVIDER_NAMES'] = ''

    # Redis defaults
    derived['SHARED_REDIS_HOST'] = 'matrix_redis'
    derived['SHARED_REDIS_PORT'] = '6379'
    derived['SHARED_REDIS_URL'] = f"redis://{derived['SHARED_REDIS_HOST']}:{derived['SHARED_REDIS_PORT']}"

    # Server IP auto-detect
    try:
        result = subprocess.run(['curl', '-fsSL', '--max-time', '10', 'https://api4.ipify.org'],
                              capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            derived['SERVER_IP'] = result.stdout.strip()
        else:
            raise Exception()
    except:
        try:
            result = subprocess.run(['curl', '-fsSL', '--max-time', '10', 'https://ifconfig.me'],
                                  capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                derived['SERVER_IP'] = result.stdout.strip()
            else:
                raise Exception()
        except:
            derived['SERVER_IP'] = 'REPLACE_WITH_YOUR_PUBLIC_IP'

    return derived

def load_secrets():
    """Load existing secrets from state dir"""
    secrets_file = STATE_DIR / 'secrets.yaml'
    if secrets_file.exists():
        with open(secrets_file) as f:
            return yaml.safe_load(f) or {}
    return {}

def generate_secret():
    """Generate a random secret"""
    return secrets.token_hex(32)

def create_secrets(config, existing):
    """Generate missing secrets and save"""
    secrets = existing.copy()

    # Core secrets
    secret_keys = [
        'POSTGRES_PASSWORD',
        'REGISTRATION_SHARED_SECRET',
        'MACAROON_SECRET_KEY',
        'FORM_SECRET',
        'COTURN_SECRET',
        'LIVEKIT_SECRET'
    ]

    for key in secret_keys:
        if key not in secrets:
            secrets[key] = generate_secret()

    # LiveKit key (not secret, but fixed)
    secrets['LIVEKIT_KEY'] = 'matrix'

    # Module secrets will be added later

    # Save
    STATE_DIR.mkdir(exist_ok=True)
    with open(STATE_DIR / 'secrets.yaml', 'w') as f:
        yaml.dump(secrets, f, default_flow_style=False)

    return secrets

def render_env(config, derived, secrets):
    """Render .env file"""
    env_vars = {}

    # Matrix config
    env_vars['MATRIX_DOMAIN'] = config['matrix']['domain']
    env_vars['SERVER_NAME'] = derived['SERVER_NAME']
    env_vars['ADMIN_USERNAME'] = config['matrix']['admin_username']

    # Derived
    env_vars.update(derived)

    # Secrets
    env_vars.update(secrets)

    # Write .env
    with open(ENV_FILE, 'w') as f:
        f.write('# matrix-easy-deploy environment\n')
        f.write(f'# Generated by apply.py on {os.popen("date -u +\"%Y-%m-%d %H:%M UTC\"").read().strip()}\n')
        f.write('# Keep this file private — it contains secrets.\n\n')
        for k, v in env_vars.items():
            f.write(f'{k}={v}\n')

    os.chmod(ENV_FILE, 0o600)

def render_template(src, dest, vars_dict):
    """Render template with {{KEY}} substitution"""
    with open(src) as f:
        content = f.read()

    for key, value in vars_dict.items():
        content = content.replace('{{' + key + '}}', str(value))

    with open(dest, 'w') as f:
        f.write(content)

def render_templates(config, derived, secrets):
    """Render all core templates"""
    # Combine all vars like env_vars
    all_vars = {}

    # Matrix config
    all_vars['MATRIX_DOMAIN'] = config['matrix']['domain']
    all_vars['SERVER_NAME'] = derived['SERVER_NAME']
    all_vars['ADMIN_USERNAME'] = config['matrix']['admin_username']

    # Derived
    all_vars.update(derived)

    # Secrets
    all_vars.update(secrets)

    # Caddyfile
    caddy_template = PROJECT_ROOT / 'caddy' / ('Caddyfile.template' if derived['INSTALL_ELEMENT'] == 'true' else 'Caddyfile-no-element.template')
    caddy_dest = PROJECT_ROOT / 'caddy' / 'Caddyfile'
    render_template(caddy_template, caddy_dest, all_vars)

    # Check for unresolved placeholders
    with open(caddy_dest) as f:
        if '{{' in f.read():
            raise ValueError("Caddyfile still contains unresolved template placeholders")

    # Synapse homeserver.yaml
    synapse_template = PROJECT_ROOT / 'modules' / 'core' / 'synapse' / 'homeserver.yaml.template'
    synapse_dest = PROJECT_ROOT / 'modules' / 'core' / 'synapse' / 'homeserver.yaml'
    render_template(synapse_template, synapse_dest, all_vars)

    # Element config.json if enabled
    if derived['INSTALL_ELEMENT'] == 'true':
        element_template = PROJECT_ROOT / 'modules' / 'core' / 'element' / 'config.json.template'
        element_dest = PROJECT_ROOT / 'modules' / 'core' / 'element' / 'config.json'
        render_template(element_template, element_dest, all_vars)

    # Coturn turnserver.conf
    coturn_template = PROJECT_ROOT / 'modules' / 'calls' / 'coturn' / 'turnserver.conf.template'
    coturn_dest = PROJECT_ROOT / 'modules' / 'calls' / 'coturn' / 'turnserver.conf'
    render_template(coturn_template, coturn_dest, all_vars)

    # LiveKit livekit.yaml
    livekit_template = PROJECT_ROOT / 'modules' / 'calls' / 'livekit' / 'livekit.yaml.template'
    livekit_dest = PROJECT_ROOT / 'modules' / 'calls' / 'livekit' / 'livekit.yaml'
    render_template(livekit_template, livekit_dest, all_vars)

    # Check for unresolved placeholders
    with open(caddy_dest) as f:
        if '{{' in f.read():
            raise ValueError("Caddyfile still contains unresolved template placeholders")

    # Synapse homeserver.yaml
    synapse_template = PROJECT_ROOT / 'modules' / 'core' / 'synapse' / 'homeserver.yaml.template'
    synapse_dest = PROJECT_ROOT / 'modules' / 'core' / 'synapse' / 'homeserver.yaml'
    render_template(synapse_template, synapse_dest, all_vars)

    # Element config.json if enabled
    if derived['INSTALL_ELEMENT'] == 'true':
        element_template = PROJECT_ROOT / 'modules' / 'core' / 'element' / 'config.json.template'
        element_dest = PROJECT_ROOT / 'modules' / 'core' / 'element' / 'config.json'
        render_template(element_template, element_dest, all_vars)

    # Coturn turnserver.conf
    coturn_template = PROJECT_ROOT / 'modules' / 'calls' / 'coturn' / 'turnserver.conf.template'
    coturn_dest = PROJECT_ROOT / 'modules' / 'calls' / 'coturn' / 'turnserver.conf'
    render_template(coturn_template, coturn_dest, all_vars)

    # LiveKit livekit.yaml
    livekit_template = PROJECT_ROOT / 'modules' / 'calls' / 'livekit' / 'livekit.yaml.template'
    livekit_dest = PROJECT_ROOT / 'modules' / 'calls' / 'livekit' / 'livekit.yaml'
    render_template(livekit_template, livekit_dest, all_vars)

def main():
    config = load_config()
    validate_config(config)
    derived = derive_values(config)
    secrets = load_secrets()
    secrets = create_secrets(config, secrets)
    render_env(config, derived, secrets)
    render_templates(config, derived, secrets)

    print("Configuration applied successfully.")
    print("Generated .env file and rendered templates.")

if __name__ == '__main__':
    main()