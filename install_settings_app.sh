#!/bin/bash
#
# Spin Rewards — settings_app installer
#
# This script unpacks the entire apps/settings_app/ folder into your project.
# Run from your project root:
#
#     cd ~/Documents/spinrewards-backend
#     bash install_settings_app.sh
#
# It will:
#   1. Create apps/settings_app/ with all 7 files (models, services, views, etc.)
#   2. Confirm by listing what was created
#
# Safe to run multiple times — will overwrite existing settings_app/ files.
#

set -e

# Confirm we're at the project root
if [ ! -d "apps" ]; then
    echo "Error: This doesn't look like the spinrewards-backend project root."
    echo "Expected to find an 'apps' directory here."
    echo "Run this from ~/Documents/spinrewards-backend"
    exit 1
fi

echo "→ Installing apps/settings_app/..."

# Decode and unpack the embedded tarball
echo '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' | base64 -d | tar -xzf - -C apps/

# Verify
if [ -d "apps/settings_app" ]; then
    echo ""
    echo "✓ Done! Created files:"
    find apps/settings_app -type f | sort
    echo ""
    echo "Next steps:"
    echo "  1. Add 'apps.settings_app' to INSTALLED_APPS in config/settings/base.py"
    echo "  2. Add to config/urls.py:"
    echo "       path('api/v1/admin/settings/', include('apps.settings_app.urls')),"
    echo "  3. Run: docker compose run --rm api python manage.py makemigrations settings_app"
    echo "  4. Run: docker compose run --rm api python manage.py migrate"
    echo "  5. Run: docker compose run --rm api python manage.py seed_settings"
else
    echo "✗ Something went wrong — apps/settings_app/ wasn't created."
    exit 1
fi
