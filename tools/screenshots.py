#!/usr/bin/env python3
"""Regenerate the README screenshots in docs/screenshots/ from made-up data.

It borrows the running shell for a few seconds: it saves the user's agenda config, points the widget
at tools/demo_events.py (the `command` source), takes the shots with the omarchy-plugin-dev skill's
plugin_shot.py, then puts the config back byte for byte and checks it. The config is restored even if
a step fails. It also moves to an empty workspace for the few seconds the shots take, so the panel
is over the wallpaper and not over your windows, and returns to yours. Run it from the repo, with
the plugin deployed and enabled.

    python3 tools/screenshots.py --shot-script <path to plugin_shot.py>

Writes docs/screenshots/{panel,settings,bar}.png and preview.png (a copy of panel.png). Nothing on the images comes from a real calendar.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.expanduser("~/.config/omarchy/agenda/config.json")
PLUGIN = "io.github.danitrrga.agenda"
SHELL = "/usr/share/omarchy/shell"


def ipc(*words):
    r = subprocess.run(["qs", "ipc", "-p", SHELL, "call", PLUGIN, *words], capture_output=True, text=True)
    return r.stdout


def state():
    return json.loads(ipc("state"))


def write_config(obj_or_bytes):
    data = obj_or_bytes if isinstance(obj_or_bytes, bytes) else json.dumps(obj_or_bytes).encode()
    tmp = CONFIG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, CONFIG)


def wait_for(pred, what, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred(state()):
                return
        except (ValueError, KeyError):
            pass
        time.sleep(0.4)
    sys.exit("timed out waiting for " + what)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--shot-script", required=True, help="path to omarchy-plugin-dev's plugin_shot.py")
    args = ap.parse_args()
    out = os.path.join(REPO, "docs", "screenshots")
    os.makedirs(out, exist_ok=True)

    original = open(CONFIG, "rb").read() if os.path.exists(CONFIG) else None
    shot = [sys.executable, args.shot_script, PLUGIN]
    # The panel is translucent, so whatever is behind it ends up in the image. Shoot over an empty
    # workspace (just the wallpaper) and come back afterwards.
    workspace = json.loads(subprocess.run(["hyprctl", "activeworkspace", "-j"], capture_output=True,
                                          text=True).stdout)["id"]
    # Hyprland here is configured in Lua: a dispatcher is a Lua expression, not `workspace empty`.
    subprocess.run(["hyprctl", "dispatch", 'hl.dsp.focus({ workspace = "empty" })'], capture_output=True)
    time.sleep(0.8)
    try:
        # Panel and bar: the demo week through the `command` source.
        # shlex.join quotes any space in a path: the command line is split like a shell would.
        demo = shlex.join(["/usr/bin/python3", os.path.join(REPO, "tools", "demo_events.py")])
        write_config({"source": "command", "command": {"line": demo}})
        wait_for(lambda s: s["source"] == "command" and s["configured"] and s["state"] == "now"
                 and s["main"]["title"] == "Design review", "the demo week to load")
        subprocess.run(shot + ["--out", os.path.join(out, "panel.png")], check=True)
        # The marketplace lists a preview.png beside manifest.json.
        shutil.copyfile(os.path.join(out, "panel.png"), os.path.join(REPO, "preview.png"))
        # Settings: an empty iCal form, so no path or credential of the user's is on the image.
        write_config({"source": "ics"})
        wait_for(lambda s: not s["configured"], "the empty configuration")
        subprocess.run(shot + ["--out", os.path.join(out, "settings.png")], check=True)
        # The bar, back on the demo week.
        write_config({"source": "command", "command": {"line": demo}})
        wait_for(lambda s: s["configured"] and s["state"] == "now", "the demo week again")
        st = state()
        left = int(st["screenX"])
        subprocess.run(shot + ["--out", os.path.join(out, "bar.png"), "--bar",
                               "--crop-x", "%d:%d" % (max(0, left - 20), left + int(st["buttonWidth"]) + 20)], check=True)
    finally:
        subprocess.run(["hyprctl", "dispatch", 'hl.dsp.focus({ workspace = "%s" })' % workspace], capture_output=True)
        if original is not None:
            write_config(original)
            ok = open(CONFIG, "rb").read() == original and (os.stat(CONFIG).st_mode & 0o777) == 0o600
            print("config restored byte for byte:", ok)
            ipc("refresh")
        elif os.path.exists(CONFIG):
            os.unlink(CONFIG)
    print("wrote", ", ".join(sorted(os.listdir(out))))


if __name__ == "__main__":
    main()
