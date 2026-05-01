#!/usr/bin/env bash
#
# Cleanup synthetic guest users from Keycloak jan realm.
#
# Usage:
#   ./scripts/cleanup-keycloak-guests.sh
#
# Environment variables:
#   KC_BASE       - Keycloak base URL (default: http://localhost:8085)
#   KC_ADMIN_USER - Admin username (default: admin)
#   KC_ADMIN_PASS - Admin password (default: admin)
#

set -euo pipefail

KC_BASE="${KC_BASE:-http://localhost:8085}"
KC_ADMIN_USER="${KC_ADMIN_USER:-admin}"
KC_ADMIN_PASS="${KC_ADMIN_PASS:-admin}"
KC_REALM="jan"
KC_ADMIN_CLIENT="admin-cli"

echo "Keycloak Realm Cleanup: $KC_BASE / $KC_REALM"
echo "Admin User: $KC_ADMIN_USER"
echo ""

# Authenticate
echo "[1/3] Authenticating to Keycloak admin API..."
TOKEN=$(/usr/bin/curl -s -X POST "$KC_BASE/realms/master/protocol/openid-connect/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "grant_type=password&client_id=$KC_ADMIN_CLIENT&username=$KC_ADMIN_USER&password=$KC_ADMIN_PASS" \
  | /usr/bin/python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "❌ Authentication failed. Check credentials and Keycloak availability."
  exit 1
fi

echo "✓ Authenticated"
echo ""

# Fetch all users
echo "[2/3] Fetching users from realm '$KC_REALM'..."
USERS_JSON=$(/usr/bin/curl -s -H "Authorization: Bearer $TOKEN" \
  "$KC_BASE/admin/realms/$KC_REALM/users?max=500")

# Extract guest user IDs
GUEST_IDS=$(echo "$USERS_JSON" | /usr/bin/python3 -c "import sys,json,re; \
users=json.load(sys.stdin); \
pat=re.compile(r'^guest-.*@temp\\.jan\\.ai$'); \
guests=[u for u in users if pat.match((u.get('username') or '').strip())]; \
print('\\n'.join([u.get('id') for u in guests]))")

GUEST_COUNT=$(echo "$GUEST_IDS" | grep -c . || true)

if [ "$GUEST_COUNT" -eq 0 ]; then
  echo "✓ No guest users found. Realm is clean."
  exit 0
fi

echo "✓ Found $GUEST_COUNT guest user(s) to delete:"
echo ""

# Delete each guest user
echo "[3/3] Deleting guest users..."
DELETED=0
FAILED=0

while IFS= read -r USER_ID; do
  if [ -z "$USER_ID" ]; then continue; fi
  
  # Get username for logging
  USERNAME=$(/usr/bin/curl -s -H "Authorization: Bearer $TOKEN" \
    "$KC_BASE/admin/realms/$KC_REALM/users/$USER_ID" | \
    /usr/bin/python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('username','N/A'))")
  
  # Delete
  HTTP_CODE=$(/usr/bin/curl -s -o /dev/null -w '%{http_code}' -X DELETE \
    "$KC_BASE/admin/realms/$KC_REALM/users/$USER_ID" \
    -H "Authorization: Bearer $TOKEN")
  
  if [ "$HTTP_CODE" = "204" ]; then
    echo "  ✓ Deleted: $USERNAME"
    ((DELETED++))
  else
    echo "  ❌ Failed: $USERNAME (HTTP $HTTP_CODE)"
    ((FAILED++))
  fi
done < <(echo "$GUEST_IDS")

echo ""
echo "Summary:"
echo "  Deleted: $DELETED"
echo "  Failed:  $FAILED"

if [ "$FAILED" -gt 0 ]; then
  exit 1
fi

echo ""
echo "✓ Cleanup complete. Realm is now clean."
exit 0
