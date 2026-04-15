#!/bin/bash
# Docker entrypoint: bootstrap config files into the mounted volume, then run hermes.
set -e

HERMES_HOME="/opt/data"
INSTALL_DIR="/opt/hermes"

# Create essential directory structure.  Cache and platform directories
# (cache/images, cache/audio, platforms/whatsapp, etc.) are created on
# demand by the application — don't pre-create them here so new installs
# get the consolidated layout from get_hermes_dir().
mkdir -p "$HERMES_HOME"/{cron,sessions,logs,hooks,memories,skills}

# .env
if [ ! -f "$HERMES_HOME/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
fi

# config.yaml
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
    cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
fi

# SOUL.md
if [ ! -f "$HERMES_HOME/SOUL.md" ]; then
    cp "$INSTALL_DIR/docker/SOUL.md" "$HERMES_HOME/SOUL.md"
fi

# Sync bundled skills (manifest-based so user edits are preserved)
if [ -d "$INSTALL_DIR/skills" ]; then
    python3 "$INSTALL_DIR/tools/skills_sync.py"
fi

# Ensure correct config.yaml settings
python3 -c "
import yaml
cfg_path = '$HERMES_HOME/config.yaml'
with open(cfg_path) as f:
    cfg = yaml.safe_load(f) or {}
changed = False
if cfg.get('group_sessions_per_user') is not False:
    cfg['group_sessions_per_user'] = False
    changed = True
# Use Groq for STT if GROQ_API_KEY is available
import os
if os.environ.get('GROQ_API_KEY') and cfg.get('stt', {}).get('provider') != 'groq':
    cfg.setdefault('stt', {})
    cfg['stt']['provider'] = 'groq'
    cfg['stt']['enabled'] = True
    # Remove model override that might force OpenAI
    cfg['stt'].pop('model', None)
    changed = True
if changed:
    with open(cfg_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False)
" 2>/dev/null || true

# Default to gateway mode for headless deployments (no TTY available)
if [ $# -eq 0 ]; then
    set -- gateway run
fi
exec hermes "$@" -v
