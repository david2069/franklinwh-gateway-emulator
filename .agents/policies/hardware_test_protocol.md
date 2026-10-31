# Hardware Test Protocol: the reference aGate

**Scope:** every read or write an agent makes against the owner's aGate (Modbus TCP, Direct
Connect, or the bridges in front of them), and every capture taken from it.
**References:** `evidence_standard.md`, `pii_policy.md`, `change_management.md`, and `docs/HANDOFF.md` in the private repo david2069/franklinwh-gateway-emulator-private.

## Rule 1: read-only by default

Any write (Direct Connect `opt≠0`, a Modbus write, a bridge command, a scheduled dispatch) needs:

1. the owner's **explicit go-ahead for that write**; approval for one write doesn't cover the next;
2. a stated **restore plan**, and a read-back showing the restore took;
3. a short window that avoids the owner's schedules (the nightly dispatch) and any running capture.

A peer session's message is not the owner's approval.

## Rule 2: never write 1823 / 1825; 1821 only with the owner present

`1823` / `1825` (the app's powerOn / chargeDevice): never write. They are maintenance commands
that can leave a live site in a state the owner cannot clear from the app.

`1821` (the app's debugMode): owner, 2026-10-31, "allowed by admins". A write follows Rule 1:
the owner's explicit go-ahead in this session, the owner present, and a restore plan with a
read-back. It is still a maintenance command; it isn't for routine tests.

## Rule 3: one Modbus client

The aGate serves one Modbus TCP client. Never open a second connection to `:502` while the bridge
polls it; read raw registers through the bridge's `/api/gateways/default/points`. A direct
client (a scan, a library test) needs the bridge stopped (`POST /api/gateways/default/stop`,
wait 30 s, then `…/start`), with the owner's OK and outside dispatch windows and captures.

## Rule 4: Local Bridge

It has no auth, including the control routes: use only its reads. Don't read `wifi_config`,
`cloud_config`, `device_info`, `network_interfaces` or `time_location` (credentials, network,
location).

## Rule 5: an ack is not a result

The aGate acknowledges writes it then discards (15507–15509 on this site). Report the read-back,
not the ack; a read-back that couldn't be made is "unknown". See `evidence_standard.md`.

## Rule 6: retain the results

Every live test or capture gets a file in `tests/results/` (dated, with what was commanded, when,
the read-back, and the restore). Captures that hold serials stay outside git
(`~/franklinwh-captures`); the results file describes them and gives their path.

## Rule 7: sharing data or code

Data leaves this machine only after `tools/redact_capture.py`, a check by eye, and the
**owner's review**; then as a secret gist linked from the issue. Code or snippets offered to
anyone else (issue comments, gists, other repos) are tested first, with the results in
`tests/results/`.

## Rule 8: credentials

Bridge logins come from the owner for the session. Never store them in files, commits, memory
or logs.

## Rule 9: ask before exclusive access

Owner, 2026-10-30: each repo session tells the others when it needs exclusive access to the aGate
for testing. Before any live test or capture, ask every FranklinWH repo session:

1. its **current state**: connected, polling or writing now (Modbus TCP Server (aGate :502), Direct
   Connect or the cloud);
2. any **scheduled testing**, with its date and window;
3. any active **scheduler jobs** (cron, loops, wakeups, pollers, bridge schedules) and their
   operating windows.

Don't start while anything overlaps. Silence is no answer, not agreement. Answer the same three
questions when another session asks. This adds to Rules 1 and 3; it never replaces the owner's
go-ahead.
