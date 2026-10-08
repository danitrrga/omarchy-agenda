# Agenda: working on this plugin

An Omarchy 4 shell plugin: a bar widget showing the running calendar block and the next one, with a
day/week panel. `README.md` says what it does; `SETUP-WITH-AGENT.md` is what the plugin's own
"Set up with my agent" flow hands to a coding agent.

## Layout

- `BarWidget.qml`: bar entry point and the panel contract. `Panel.qml`: the popup. `DayPane.qml`: one day (the panel keeps two, for the slide). `Settings.qml`: choosing the calendar source. `AgendaFace.qml`, `KeyCap.qml`: small pieces.
- `Model.js`: all calendar logic, pure, takes `now` as an argument. `agenda_ctl.py`, `fetch_ics.py`: the back end (three sources behind one command), standard library only.
- `tests/`: `model_test.js` (node), `test_fetch_ics.py`, `test_ctl.py` (a local server stands in for Google).

## Work loop

The repo is the source; the shell loads a copy from `~/.config/omarchy/plugins/<id>/` (it cannot be a
symlink). Use the `omarchy-plugin-dev` skill: deploy with its `scripts/deploy_plugin.py <repo> --restart`
(a restart is required after any QML change), watch `qs log -p /usr/share/omarchy/shell`, and drive the
widget through its IPC target `io.github.danitrrga.agenda` (`state`, `open`, `settings true`, `day N`, `move N`).

## Rules that bit before

- A test is not evidence until it has been seen to fail: mutate the code, watch the test go red.
- Credentials live only in `~/.config/omarchy/agenda/config.json` (mode 600). Never in an error message, never in the IPC `state()`, never in argv.
- English UI; calendar event text is shown exactly as the calendar has it.
- Before publishing, the manifest id becomes `io.github.<user>.agenda`, and every `moduleName`, IPC target and doc changes with it.

## Tests

    TZ=Europe/Amsterdam node tests/model_test.js Model.js
    python3 tests/test_fetch_ics.py
    python3 tests/test_ctl.py
