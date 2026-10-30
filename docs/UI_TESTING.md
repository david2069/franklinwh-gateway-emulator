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

- There are 7 cards: `site`, `us-split`, `au-3phase`, `au-2phase`, `tou-reserve`, `fresh`, `spec`.
- `fresh` is a factory aGate: its card says **Modbus TCP off**. Like a real one, it serves only
  Direct Connect until a Direct Connect `1205` `sunsMdEn` write turns Modbus TCP on (the
  **der-comms** form with modbus `true` does the same through the control API).
- `site` has appliances (`loads: sydney-family`): hot water on SW1, EV on SW2, aircon on SW3.
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
| Inputs → **pv** | solar_w `6000` | Green "Applied …". In daylight at the gateway, solar rises to at most what the sun gives then; at night it stays 0 and the tag says "capped … by the sun" |
| Inputs → **load** | w `4500` | The card's Home shows 4.50 kW |
| Loads → **circuit** | n `2`, on `false` | The SW2 box greys out |
| Grid and phases → **grid** | available `false` | The card shows "islanded"; the Grid box turns red; no grid flow |
| Grid and phases → **grid** | restore `true` | Grid connected; grid flow resumes after the grid code's reconnect delay (60 s for `site` on Ausgrid) |
| Alarms and faults → **alarms** | set `OVER_TEMP` | A red OVER_TEMP tag on the card |
| Inputs → **load** | w `abc` | Red "w: not a number"; nothing is sent |
| Loads → **circuit** | n `2` only | Red "circuit: missing ['on']", the emulator's own answer |
| Any form → **Example** | pick one | The fields fill with a working example |

## 5. TOU schedule

Under the gateway's tables, **TOU schedule** shows the day as two bars (dispatch and tariff),
a line at the gateway's local time, and the blocks with their prices. It applies only in TOU
mode; the line beside the heading says which mode the gateway is in.

- Select `tou-reserve` and press **Edit**. One row per block; gaps (shown on the bars) run SELF,
  off-peak, and stay gaps.
- Set the first block's dispatch to `7 GRID_EXPORT`, press **Add block** (it takes the second
  half of the last block), type `0.55` as the On-Peak buy price and press **Send**: green
  "Schedule applied …"; the bars and table change.
- Edit again and set the last block's start to `10:00`, then **Send**: red "tou: … overlaps …",
  the emulator's own answer, and the editor stays open with what you typed.
- **Reset to default** brings back off-peak SOLAR and 17:00–21:00 SELF. **Cancel** drops the
  edit.
- Battery and modes → **mode** `3`: the gateway follows the block under the line.

## 6. With a bridge (optional)

Point the Modbus bridge at a card's Modbus TCP address (for `site`, `127.0.0.1:5020`), or the
Direct Connect bridge at its Direct Connect address (every aGate has one: 9020–9026 in the
example; `spec` is not an aGate). For `fresh`, enable Modbus TCP through Direct Connect first. Fire actions here and watch the bridge
react. The `ctl` command works against the same port (`franklinwh-emulator ctl site status`).

## 7. Save and restore

- Set something up (step 4), type a name in the header (or leave it blank) and press **Save**.
  The line under the header says where it went (`emulator-workspace/sessions/<name>.yaml`).
- Press Ctrl-C and start the UI again. The open page says the emulator restarted; reload it.
- Pick the session in the header and press **Restore**: everything you set comes back, and the
  line says how many actions applied (and which failed, if the fleet changed).
- Or start with it: `franklinwh-emulator ui examples/fleet.yaml --restore <name>`.
- **Activity** (top of the actions column) lists every action this run, from any source.

## 8. Undo

- pv solar_w `null` and load w `null` hand solar and load back to the model.
- Alarms → reset `true` clears latched alarms; clear `all` clears the rest.
- Or Ctrl-C and start again: every start is fresh.

## Reporting a problem

Note the step, the gateway, what you entered and what you saw (a screenshot helps), and the
terminal output if there is any.
