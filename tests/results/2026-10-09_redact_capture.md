# redact_capture.py verification, 2026-10-09

Change: commit 9fc73ca (tools/redact_capture.py, tests/test_redact_capture.py).
Trigger: a redaction snippet posted on franklinwh-modbus#18 masked long decimals, and was fixed by its recipient. Before sharing the 2026-10-08 paired capture, the redactor in this repo was tested against identifiers it could plausibly meet.

Test IDs were renamed afterwards (fake MAC/IP fixtures replaced, see the PII scanner commit); results are unchanged.

## 1. New tests against the old code (7f12929 version)

```
FAILED tests/test_redact_capture.py::test_identifying_keys_are_masked[ipAddr]
FAILED tests/test_redact_capture.py::test_identifying_keys_are_masked[location]
FAILED tests/test_redact_capture.py::test_identifying_keys_are_masked[postcode]
FAILED tests/test_redact_capture.py::test_identifying_keys_are_masked[city]
FAILED tests/test_redact_capture.py::test_identifying_keys_are_masked[street]
FAILED tests/test_redact_capture.py::test_dict_keys_that_are_serials_are_masked
FAILED tests/test_redact_capture.py::test_identifiers_in_text_are_masked[serial_underscore]
FAILED tests/test_redact_capture.py::test_identifiers_in_text_are_masked[ipv4]
FAILED tests/test_redact_capture.py::test_identifiers_in_text_are_masked[ipv6]
FAILED tests/test_redact_capture.py::test_identifiers_in_text_are_masked[mac_colon]
FAILED tests/test_redact_capture.py::test_identifiers_in_text_are_masked[mac_dash]
FAILED tests/test_redact_capture.py::test_identifiers_in_text_are_masked[email]
FAILED tests/test_redact_capture.py::test_every_must_be_positive
FAILED tests/test_redact_capture.py::test_window_across_midnight
FAILED tests/test_redact_capture.py::test_blank_lines_are_skipped
15 failed, 54 passed in 0.07s
```

## 2. New tests against the new code

```
tests/test_redact_capture.py::test_identifying_keys_are_masked[sn] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[SN] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[1.SN] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[fhpSn] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[devSn] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[serial] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[serialNumber] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[bat_sn] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[mac] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[wifiMac] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[ip] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[lan_ip] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[ipAddr] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[lfdi] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[sfdi] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[pin] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[ssid] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[lat] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[latitude] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[lon] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[lng] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[longitude] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[address] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[email] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[password] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[token] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[location] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[postcode] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[zip] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[city] PASSED
tests/test_redact_capture.py::test_identifying_keys_are_masked[street] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[p_sun] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[soc] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[fhpPower] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[p_fhp] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[mode] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[run_status] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[TmpSnk] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[701.TmpSnk] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[electricity_type] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[flat] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[isTripPhsAEn] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[dspRunstatus] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[remoteSolarEn] PASSED
tests/test_redact_capture.py::test_measurement_keys_are_kept[installPV1port] PASSED
tests/test_redact_capture.py::test_nested_and_list_values PASSED
tests/test_redact_capture.py::test_dict_keys_that_are_serials_are_masked PASSED
tests/test_redact_capture.py::test_identifiers_in_text_are_masked[serial] PASSED
tests/test_redact_capture.py::test_identifiers_in_text_are_masked[serial_underscore] PASSED
tests/test_redact_capture.py::test_identifiers_in_text_are_masked[ipv4] PASSED
tests/test_redact_capture.py::test_identifiers_in_text_are_masked[ipv6] PASSED
tests/test_redact_capture.py::test_identifiers_in_text_are_masked[mac_colon] PASSED
tests/test_redact_capture.py::test_identifiers_in_text_are_masked[mac_dash] PASSED
tests/test_redact_capture.py::test_identifiers_in_text_are_masked[email] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[V10R01B04D00] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[V12R02B30D06] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[-0.12345678901234567] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[1791442816.169296] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[2026-10-09T16:00:00+11:00] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[18:00:12] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[Self-Consumption] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[aGate X] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[FranklinWH Technologies Co., Ltd] PASSED
tests/test_redact_capture.py::test_ordinary_text_is_kept[Command Sent: -5000W (100.0% of 5000W)] PASSED
tests/test_redact_capture.py::test_numbers_and_bools_untouched PASSED
tests/test_redact_capture.py::test_every_keeps_slow_reads PASSED
tests/test_redact_capture.py::test_every_must_be_positive PASSED
tests/test_redact_capture.py::test_window_across_midnight PASSED
tests/test_redact_capture.py::test_blank_lines_are_skipped PASSED
============================== 69 passed in 0.03s ==============================
```

## 3. Full suite

```
291 passed in 15.20s
```

## 4. Real capture, old vs new redactor

Input: ~/franklinwh-captures/paired_2026-10-08_joined.jsonl (7,607 samples; not in the repo, it holds serials).

```
old  new  field
7606 7606 /dc_1301/fhpSn
7597 7597 /mb/1.SN
7599 7599 /mb/serial
outputs byte-identical: True
```

Result: the old redactor missed the identifiers listed in section 1. On this capture both versions produce the same output, so the 2026-10-08 capture was not affected.
