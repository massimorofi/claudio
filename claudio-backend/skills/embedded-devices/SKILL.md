---
name: embedded-devices
description: Summary of all skills for onboarding and iterating on small embedded
  hardware — M5Stack ESP32 device onboarding (USB detect, flash UIFlow 2.0, install
  the companion app) and iterating on the Cardputer-Adv MicroPython app bundle. Use
  when asked to flash or set up an M5Stack/ESP32 device, bring up a Cardputer, install
  or update MicroPython apps on it, or iterate on a device app after initial provisioning.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: cwc-makers
    name: m5-onboard
    license: unknown
  - owner: cwc-makers
    name: cardputer-buddy
    license: unknown
---

# Embedded Devices (ESP32 / Cardputer)

Physical hardware adds failure modes software never has: the device is not
detected, the flash failed halfway, the firmware version does not match the
tooling. Every step must be *confirmed on the device*, not assumed from the
host.

## Constituent skills (2)

| Skill | Origin | What it contributes |
|---|---|---|
| m5-onboard | cwc-makers | End-to-end onboarding of a fresh M5Stack ESP32: USB detect, flash UIFlow 2.0, install Claude Buddy |
| cardputer-buddy | cwc-makers | Iterates on the Cardputer-Adv MicroPython app bundle (Claude Buddy, Snake, Hello) after provisioning |

## Consolidated guidance

1. **Confirm detection before flashing.** Verify the USB serial device is
   actually present and identified (port, chip) before any write. Flashing
   the wrong port is the classic wasted hour.
2. **Provisioning and iteration are separate phases.** `m5-onboard` gets a
   fresh device to a known base state; `cardputer-buddy` only works *after*
   that base state exists. Do not skip the base and expect iteration to
   work.
3. **Flash is not atomic in practice.** A half-written flash leaves a
   device that looks present but does not boot. Verify boot after every
   flash, not just the tool's exit code.
4. **Firmware/tooling versions must match.** UIFlow 2.0, the MicroPython
   runtime and the app bundle are a version triangle; a mismatch shows up
   as bizarre runtime errors, not a clean version error.
5. **Iterate on-device with fast feedback.** Small change → push → observe
   on the actual hardware. Long un-flashed stretches of MicroPython
   development produce code that was never run.
6. **Keep a known-good baseline.** After a successful provision, save the
   working image/config so a failed experiment is recoverable in one step.
7. **Hardware assumptions need physical verification.** Button mapping,
   display orientation, keyboard behaviour: check on the device, not from
   the datasheet.

## When to use this skill

"Set up this M5Stack", "flash this ESP32", "get the Cardputer working",
"update the app on the device", "the device isn't showing up".

## Note

Only two inventoried skills fall in this area; both come from the
`cwc-makers` plugin in the downloaded (not installed) marketplace clone.

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| cwc-makers | m5-onboard | unknown |
| cwc-makers | cardputer-buddy | unknown |
