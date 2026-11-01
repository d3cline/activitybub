#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
app_dir="$(pwd -P)"
if [[ "$app_dir" == *$'\n'* || "$app_dir" == *'%'* || "$app_dir" == *'"'* || "$app_dir" == *'`'* || "$app_dir" == *'$'* || "$app_dir" == *'\'* ]]; then
  echo 'Move Activity Bub to a folder without quotes, percent signs, backslashes or shell expansion characters first.' >&2
  exit 1
fi
desktop_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$desktop_dir"
cat > "$desktop_dir/activity-bub.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Activity Bub
Comment=Explore the public Fediverse
Exec="$app_dir/run.sh"
Icon=$app_dir/activity_bub/assets/activity-bub.svg
Terminal=false
Categories=Network;News;Qt;
StartupWMClass=activity-bub
EOF
chmod +x run.sh "$desktop_dir/activity-bub.desktop"
echo 'Activity Bub added to your application menu. Keep this folder in place.'
