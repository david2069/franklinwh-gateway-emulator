# Fleet and scenario validation; gateway config description (#18, PR B)

**Changed:** new `src/franklinwh_emulator/validate.py`: `config_schema()` (every gateway key:
type and default from `GatewayConfig`/`SiteConfig`, one-line doc), `validate_fleet`,
`validate_scenario` (every problem as `{path, error}`; sites built at the run's start time, no
servers). `GET /schema` adds `gateway_config`. README section, CHANGELOG.

**Tests:** new `tests/test_validate.py` (26 cases): `examples/fleet.yaml` clean and buildable;
all 17 built-in scenarios clean; one bad fleet reports 9 problems at once (unknown top-level,
defaults and gateway keys; bad weather; duplicate id; Modbus and Direct Connect port clashes;
unknown profile; non-mapping entry); fleet shape errors; one bad scenario reports 5 problems at
once (fleet key, step body, unknown action, missing gateway, missing required key); scenario
structure errors; config description covers every field with a doc; `/schema` JSON.

Full suite: **405 passed** (Python 3.12, local).

**Break checks** (each temporary, then restored):
1. Stop at a gateway's first unknown keys → `test_every_fleet_problem_reported_at_once` failed.
2. One doc removed (`offgrid_floor_soc`) → `test_config_schema_covers_every_field_with_a_doc` failed.
3. Step body check removed → `test_every_scenario_problem_reported_at_once` failed.

**Note:** an `open-meteo` gateway that isn't `offline` fetches the run's start day while being
validated, as starting it would (documented in the module).
