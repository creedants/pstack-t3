# Verification surfaces

Read this file before you verify a change in a UI, on a device, or in a CLI, and use the surface it names.

- Web or Electron UI: `preview_open` the dev server URL, then `preview_snapshot`, `preview_click`, `preview_type`, `preview_press`, `preview_wait_for`, `preview_evaluate`. Use `preview_hover` to reveal a menu or tooltip, `preview_drag` to drop one element on another, `preview_select` to choose an option in a native select, `preview_upload` to give the page files, and `preview_dialog` to accept or dismiss a browser dialog. Record proof with `preview_recording_start` and `preview_recording_stop`. Check `preview_status` first. A call can fail because no preview host is available. Call `preview_status` again. Call `preview_open` if it lists no tab. Report a failure that persists. Never count that check as verified. Close each preview you opened with `t3_preview_close` and its `tabId`.
- Preview tabs. Pass the `tabId` that `preview_open` returns on every later preview call. Without it, T3 chooses the tab for you, which can be one you did not mean. The one exception is a new tab, which takes no `tabId` (see Clean state). Children that run in parallel on one provider session each open their own tab with `reuseExistingTab: false`. Act only on tabs you opened. Never act on a tab whose `owner` in `preview_status` is `human`, and treat another session's tab as read-only.
- **Stale tabs.** Each thread owns its tabs, and a `tabId` that no longer resolves fails an open call that carries it without `reuseExistingTab: false` instead of acting on another tab. Treat a server tab failure as a lost tab. A desktop runtime tab error means the tab is not controllable here, so omit `tabId` and open a server tab. Call `preview_status`, open a new tab with `reuseExistingTab: false`, and repeat the check from its first step.
- Preview images. Pass `includeImage` explicitly on every `preview_snapshot`, because its default differs between builds. Pass `includeImage: true` when you need to see the page. When the result holds no image, or you need a PNG file, pass `save: true` and open the file at the `screenshotPath` it returns.
- **Masked fields.** Use synthetic credentials in a dedicated test state. Never capture a populated field that holds a real password. `preview_snapshot` masks password field values. Treat a masked field as filled and hidden, never as a value to assert. The mask does not cover other page text that holds no password value, images, or `preview_evaluate` results.
- Clean state. Do not assume a tab is logged out or logged in. Check. For a logged-out or first-run check, call `preview_open` with `reuseExistingTab: false`, no `tabId`, and an `incognito` `profileId` when `preview_status` lists one. Read the page with `preview_snapshot` and confirm it shows the state you need before you rely on it. Never clear storage in a profile a human uses.
- Devices and simulators: `device_list`, `device_open`, `device_screenshot`, `device_close`.
- CLIs and TUIs: run them in the terminal and assert on output.

## Visual reports

A page is for a reply that is already due. When the playbook that runs this thread sends a reply, and a table of units, a timeline, a frontier, or a chart would carry it better than prose, publish that part as a page. A short status with no table stays text.

1. Write one self-contained HTML document. Every number and link on it comes from the store or the tables the text report would read.
2. Call `html_preview` with it. Fix every console error and every clipped or overlapping element. When the page holds a wide table, preview it again with `width: 390`.
3. Call `html_render` with the document, a `title`, and the preview's `contentHeight` as `height`, raised to 80 or capped at 2000. A capped frame scrolls the rest.
4. Write the reply after the render. It does not announce the page and adds only what the page does not show. Every decision that waits on the user, every PR link, and the store and report paths stay in the reply text, so a capped frame never hides them.

Leave `html`, `body`, and the outermost element with no background color, and style the page with the theme variables `html_render` describes. When either tool is missing or `html_render` fails, send the same facts as text.

A rendered page is a reply the user reads, even with no reply text. Render none on a wake that sends no reply. Brigade at `digest` renders no page except the one [Agent activity](../brigade/SKILL.md#agent-activity) names, so its replies stay in the plain form [Digest messages](../brigade/SKILL.md#digest-messages) sets. At every level, step 9 of brigade's [Run a service](../brigade/SKILL.md#run-a-service) decides which wakes reply before any page is drawn.
