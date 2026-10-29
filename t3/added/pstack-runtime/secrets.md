# Secrets

Read this file before you ask the user for a secret or before a shell command needs a token, deploy key, or password.

## Delivery

A shell command reads a token, deploy key, or publish credential through `request_secret` with `delivery: "file"`.
The result is a private file path.
A T3 tool that accepts a `secretRef`, such as `schedule_task`, takes the default delivery.
See [Webhook schedules](webhooks.md) for the signing secret case.
Pass a `label` naming the secret and a `reason` naming the command that reads it.
A retry after a timeout or a cancel passes a new `clientRequestId`.

## Read it inside the command

Read the file only inside the command that uses it.

```sh
TOKEN="$(cat <file>)" <cli>
```

No `echo`.
No `cat` on its own line, which prints the value to the terminal.
No `set -x`.
No export to a long-lived environment.
Delete the file after the last command that reads it, as in `rm -f <file>`, when the host allows it.
T3 removes the file at the first sweep after it is 24 hours old.
The sweep runs at startup and hourly.

## Never

Never ask for the secret in chat, and never type the value.
Never print, copy, log, or commit the file or its contents.
Never write the value or the path into a brief, a report, a commit message, or a shell profile.

## Who answers the card

The worker that needs the secret calls `request_secret` on its own thread.
A child task does the same, and the user answers the card on the child's thread.
The card appears on that thread and that thread waits.
The user types the value into the card, and the model never sees it.
A child cannot answer its own card.
A parent cannot fill it either.
`t3_pending_request` reads questions, not secret cards.
The parent names the child thread that waits, with its link.

## Host without file delivery

T3 Code 0.1.3 added `delivery: "file"`. See [Host notes](host.md#host-version).
On an older host, or when `request_secret` takes no `delivery`, ask the user to update T3 Code when they can.
When they cannot, ask them to place the secret in a file themselves.
Never ask for the value in chat.
Read that file by the rules above, and delete it only when the user allows it.
