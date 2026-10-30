# Testing the web UI by hand

A walk-through of the web UI ([UI_PLAN.md](UI_PLAN.md)) against the example fleet. It drives
**emulated gateways only**; nothing here touches a real aGate.

## 1. Start it

From the repo folder:

```bash
source .venv/bin/activate              # once per terminal; or call .venv/bin/franklinwh-emulator
franklinwh-emulator ui examples/fleet.yaml
```

It prints each gateway's addresses and then `UI on http://127.0.0.1:8502/`. Open that page and
leave the terminal running; Ctrl-C stops it. If the fleet file has a problem, it prints every
problem and exits instead.

`command not found: franklinwh-emulator` means the virtual environment isn't active (or the
package isn't installed in it: `pip install -e .` once).

## 2. Cards

- There are 6 cards: `site`, `us-split`, `au-3phase`, `au-2phase`, `tou-reserve`, `spec`.
- Each shows SoC, solar, home, battery and grid power, changing every second, and the addresses
  to point a bridge at.
- Click a card: it gets a green border, and the panel below switches to it (the **active
  gateway**). The choice is remembered when the page reloads.

## 3. Energy diagram

- Select `au-3phase`: it has remote PV, so an **aPbox** joins Remote PV 1 to the aGate.
- Dashed lines move in the direction power flows; idle lines are grey and still.
- Solar follows the site's local time (the diagram's header shows the weather). At night it is 0;
  step 4 overrides it.
- Under the diagram is the list of what the emulator doesn't model, so isn't drawn.

## 4. Actions

Below the diagram; open a group to see its forms. Each form applies to the active gateway, and
its result stays in the form until that form is used again.

| Group → form | Enter | Send, then expect |
|---|---|---|
| Inputs → **pv** | solar_w `6000` | Green "Applied …"; solar lines move; the battery charges or the grid exports |
| Inputs → **load** | w `4500` | The card's Home shows 4.50 kW |
| Loads → **circuit** | n `2`, on `false` | The SW2 box greys out |
| Grid and phases → **grid** | available `false` | The card shows "islanded"; the Grid box turns red; no grid flow |
| Grid and phases → **grid** | restore `true` | Grid connected; grid flow resumes after the grid code's reconnect delay (60 s for `site` on Ausgrid) |
| Alarms and faults → **alarms** | set `OVER_TEMP` | A red OVER_TEMP tag on the card |
| Inputs → **load** | w `abc` | Red "w: not a number"; nothing is sent |
| Loads → **circuit** | n `2` only | Red "circuit: missing ['on']", the emulator's own answer |
| Any form → **Example** | pick one | The fields fill with a working example |

## 5. With a bridge (optional)

Point the Modbus bridge at a card's Modbus TCP address (for `site`, `127.0.0.1:5020`), or the
Direct Connect bridge at its Direct Connect address. In `examples/fleet.yaml` only `site` (9020)
and `us-split` (9021) have one; the others show "Direct Connect off". Add
`directconnect: {port: <port>}` to a gateway's entry to give it a listener. Fire actions here and watch the bridge
react. The `ctl` command works against the same port (`franklinwh-emulator ctl site status`).

## 6. Undo

- pv solar_w `null` and load w `null` hand solar and load back to the model.
- Alarms → reset `true` clears latched alarms; clear `all` clears the rest.
- Or Ctrl-C and start again: every start is fresh.

## Reporting a problem

Note the step, the gateway, what you entered and what you saw (a screenshot helps), and the
terminal output if there is any.
