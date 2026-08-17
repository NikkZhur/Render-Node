#!/usr/bin/env bash

# Prepare the smallest cgroup v2 subtree the unprivileged backend needs. Failure
# is non-fatal here: strict mode will remain unavailable and expose the exact
# diagnostic without launching Blender.
prepare_render_node_cgroup() {
  local owner="$1"
  local mount_root="" mount_point="" current_path="" relative="" current_dir="" root=""
  local line left right controller enabled

  while IFS= read -r line; do
    left="${line%% - *}"
    right="${line#* - }"
    [[ "$right" == cgroup2\ * ]] || continue
    read -r _ _ _ mount_root mount_point _ <<< "$left"
    break
  done < /proc/self/mountinfo
  current_path="$(awk -F: '$1 == "0" && $2 == "" { print $3; exit }' /proc/self/cgroup)"

  if [[ -z "$mount_point" || -z "$current_path" || "$mount_point" == *\\* ]]; then
    printf '[render-node] WARNING: cgroup v2 is not available; strict rendering will stay disabled.\n' >&2
    return 0
  fi
  if [[ "$mount_root" == "/" ]]; then
    relative="${current_path#/}"
  elif [[ "$current_path" == "$mount_root" ]]; then
    relative=""
  elif [[ "$current_path" == "$mount_root/"* ]]; then
    relative="${current_path#"$mount_root"/}"
  else
    printf '[render-node] WARNING: current cgroup is outside the cgroup v2 mount root.\n' >&2
    return 0
  fi
  current_dir="${mount_point}${relative:+/${relative}}"
  [[ -r "$current_dir/cgroup.controllers" ]] || {
    printf '[render-node] WARNING: cgroup v2 controllers are not delegated to this service.\n' >&2
    return 0
  }
  for controller in memory pids; do
    grep -qw "$controller" "$current_dir/cgroup.controllers" || {
      printf '[render-node] WARNING: cgroup v2 %s controller is unavailable.\n' "$controller" >&2
      return 0
    }
    enabled="$(<"$current_dir/cgroup.subtree_control")"
    if [[ " $enabled " != *" $controller "* ]]; then
      if ! printf '+%s\n' "$controller" > "$current_dir/cgroup.subtree_control"; then
        printf '[render-node] WARNING: cannot enable cgroup v2 %s controller.\n' "$controller" >&2
        return 0
      fi
    fi
  done

  root="$current_dir/render-node"
  if ! mkdir -p -- "$root"; then
    printf '[render-node] WARNING: cannot create the delegated Render Node cgroup.\n' >&2
    return 0
  fi
  [[ -r "$root/cgroup.subtree_control" && -e "$root/cgroup.procs" ]] || {
    printf '[render-node] WARNING: delegated cgroup v2 controls are incomplete.\n' >&2
    return 0
  }
  for controller in memory pids; do
    enabled="$(<"$root/cgroup.subtree_control")"
    if [[ " $enabled " != *" $controller "* ]]; then
      if ! printf '+%s\n' "$controller" > "$root/cgroup.subtree_control"; then
        printf '[render-node] WARNING: cannot delegate cgroup v2 %s controller.\n' "$controller" >&2
        return 0
      fi
    fi
  done
  if ! chown "$owner" "$root" "$root/cgroup.procs" "$root/cgroup.subtree_control"; then
    printf '[render-node] WARNING: cannot delegate the Render Node cgroup ownership.\n' >&2
    return 0
  fi
  if [[ -e "$root/cgroup.threads" ]] && ! chown "$owner" "$root/cgroup.threads"; then
    printf '[render-node] WARNING: cannot delegate cgroup thread ownership.\n' >&2
  fi
}
