## Agent activity

When the user asks what the agents are doing, or asks for activity or status as a picture, show the activity page. That request is a message from the user, so a reply is due at every reporting level. The script builds the page from this store and T3 Code's state database.

```bash
A() { python3 "<skills>/brigade/scripts/activity.py" --at "<restaurant dir>" "$@"; }
A                 # one HTML document on stdout, for the last 3 hours
A --hours 12      # the last 12 hours
A --text          # plain lines instead of the HTML document
```

1. Run `A`. Its stdout is the whole document.
2. Call `html_preview` with that document. When the preview shows a console error or a clipped or overlapping element, send the stdout of `A --text` and name the fault in the reply. Render only a page whose preview passes. Call `html_render` with the document unchanged and the title `Agent activity`, per steps 3 to 4 of [Visual reports](../pstack-runtime/SKILL.md#visual-reports).
3. When the preview shows `This copy differs from what the tool wrote`, the `html` argument is not the script's output. Run `A` again and call `html_preview` with its stdout.
4. When `html_preview` or `html_render` is missing, or `html_render` fails, send the stdout of `A --text` as the reply.
5. On exit status 1 or 3 the script prints one line on stderr and nothing on stdout. Status 3 means it could not use T3 Code's database. Send that line as the reply. Status 1 names a fault in the arguments or the store.

The page names work items by id at every reporting level. Every string of the document and of the text form passes the script's filter, which removes each word it reads as a T3 Code thread or sub-agent id, an absolute or home-relative file path, a `file:` address, or an email address. `A --help` states what it reads as each.

For a view that stays current, run the server once and render its wrapper. Start `A --serve` in the background. It prints its token address and serves this store's page, rebuilt at most every 10 seconds. Render the stdout of `A --live-page` with `html_render`. The wrapper polls the default server address every 30 seconds, keeps the last good view while the server is unreachable, and folds each item under its header. The server stops on a T3 restart, so start it again after one. The token lives in `activity-token` under the store directory, mode 0600, reused across restarts while it stays valid and replaced otherwise. The wrapper holds the token, so never share or commit it. The wrapper sends its token to whatever listens on the port, so on a machine with other local users, do not leave a rendered wrapper polling while the server is stopped.

```bash
A --serve &                # prints http://127.0.0.1:48731/activity/<token>
A --live-page              # the wrapper that polls that address
```

## Executive chef's view

`python3 <skills>/brigade/scripts/brigade.py walk` groups every restaurant under its repository. Each repository gets one header with its landing queue's status line, or `no landing contract`. Under it, each restaurant gets its reporting level and counts, with the waiting tickets whose block still holds counted as blocked. A second line names its thread ID and the unreleased leases its dishes hold. Open decisions follow. `walk --repo <root>` prints one repository. A restaurant idle past 24 hours is marked, so a stalled head chef shows. `walk` adds `, mode <m>` after the reporting level.
