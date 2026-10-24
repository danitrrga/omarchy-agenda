# Agenda: setup guide for coding agents

You were launched from the Agenda widget's settings ("Set up with my agent"). Your job is to connect the user's calendar. Everything below is the whole interface; do not edit the QML.

## Rules

- **Never print, read back or log a credential.** Do not `cat` `config.json`. Use `config-show`, which replaces every secret with `set` and shows a command as its program plus how many arguments are hidden. Do not ask the user to paste a secret into this chat: the transcript is stored. Have them paste it into the settings form (the panel's `settings` link or the `S` key), or give them a one-line command that reads it silently (`read -rs`).
- Ask the user which of the three sources they want before changing anything.
- Test with `fetch`, and report the real result (the number of events, or the exact error line).

## Where things are

| What | Path |
|---|---|
| Config, mode 600, holds the credentials | `~/.config/omarchy/agenda/config.json` |
| What the widget reads | `~/.local/state/omarchy/agenda/day.json` |
| The tool | `~/.config/omarchy/plugins/io.github.danitrrga.agenda/agenda_ctl.py` |

Run the tool with `/usr/bin/python3 -I <path to agenda_ctl.py> <command>`.

| Command | Does |
|---|---|
| `config-show` | The config with every secret replaced by `set`. A command shows only its program and `(+N argument(s) hidden)`: its arguments are where a script keeps a token, so to check them ask the user or read the script they point to, never the config file. |
| `config-set` | Merges the JSON in the `AGENDA_PATCH` environment variable into the config. A blank credential (`url`, `client_id`, `client_secret`) leaves the stored value alone; a blank value for anything else (a command, a watch path) clears it. `source` must be `ics`, `google` or `command`. Changing the Google client ID or secret drops the stored session: sign in again. |
| `fetch --out PATH` | Reads the configured source and writes the result to `PATH`. Test with a temporary path, not the live `day.json`. |
| `google-login` | Browser sign-in for the `google` source |

The widget notices a change to `config.json` by itself and refreshes. No restart is needed.

## The three sources

Set `source` to `ics`, `google` or `command`.

### `ics`: a secret iCal address

```json
{ "source": "ics", "ics": { "url": "https://calendar.google.com/calendar/ical/.../basic.ics" } }
```

The address is a credential. Where to find it in Google Calendar: Settings, the calendar, Integrate calendar, *Secret address in iCal format*. Proton, Fastmail and Nextcloud have an equivalent. Google refreshes it slowly, sometimes hours; say so if the user expects instant updates.

### `google`: the user's own Google account

```json
{ "source": "google", "google": { "client_id": "...", "client_secret": "...", "refresh_token": "(written by google-login)" } }
```

The user creates their own OAuth client (type **Desktop app**) in their own Google Cloud project and enables the Calendar API. Then store `client_id` and `client_secret` with `config-set` and run `google-login`, which opens the browser and stores `refresh_token`. The scope is read-only. If the app is in *Testing* mode the session expires after 7 days; tell the user to publish it as *In production*.

### `command`: the user's own program

```json
{ "source": "command", "command": { "line": "python3 /path/to/script.py --out {out}", "watch": "/optional/file/whose/change/means/refresh" } }
```

The program prints one JSON document on stdout, or writes it to the path that replaces `{out}`. It runs without a shell, every 10 minutes and when the panel opens (if the last reading is over two minutes old); non-zero exit is an error and the last line of its stderr is shown to the user (so is an exit 0 that leaves no valid JSON, so a script must not swallow its own failure silently: print the cause on stderr and exit non-zero; arguments of the command are hidden in that line). If the user already has an agent, a sync or an MCP that can read their calendar, write a small script that outputs this format.

```json
{
  "as_of": "2026-10-08T16:06:06+02:00",
  "events_by_date": {
    "2026-10-08": [
      { "id": "abc", "summary": "Block title", "start": "2026-10-08T16:00:00+02:00", "end": "2026-10-08T17:30:00+02:00",
        "location": "", "description": "", "recurring": false },
      { "id": "bday", "summary": "All day", "start": "2026-10-09", "end": "2026-10-10" }
    ]
  }
}
```

`start` and `end` are ISO with a time zone, or a bare date for all-day events. Files under a date are the events that start that day; one that crosses midnight is drawn on the next day too. The widget shows seven days from today's midnight, so the script should return the blocks that already ended today as well. To flag a calendar that could not be read, add `{"error": "message"}` to a list: the panel then says so instead of showing an empty day.

## Done means

`fetch --out /tmp/agenda-test.json` exits 0 and the count of events is plausible for the user's week. Tell the user the count, and that the bar widget will update within a few seconds.
