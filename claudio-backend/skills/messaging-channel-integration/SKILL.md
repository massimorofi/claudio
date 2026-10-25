---
name: messaging-channel-integration
description: Summary of all skills for connecting Claude to chat platforms — Discord,
  Telegram and iMessage — covering bot/bridge setup, token configuration, pairing
  approval, allowlists and DM/group access policy. Use when asked to connect or set
  up Discord, Telegram or iMessage, approve a pairing, edit an allowlist, decide who
  may message the agent, or debug why a messaging channel is not responding.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: discord
    name: configure
    license: unknown
  - owner: discord
    name: access
    license: unknown
  - owner: telegram
    name: configure
    license: unknown
  - owner: telegram
    name: access
    license: unknown
  - owner: imessage
    name: configure
    license: unknown
  - owner: imessage
    name: access
    license: unknown
---

# Messaging Channel Integration

Three platforms, one identical shape. Every channel (Discord, Telegram,
iMessage) splits its concerns into exactly two skills: **configure** (the
plumbing: credentials and bridge) and **access** (the policy: who may talk
to the agent). Learn one, you have learned all three.

## Constituent skills (6)

| Skill | Platform | What it contributes |
|---|---|---|
| discord/configure | discord | Save the bot token, review access policy, bring the channel up |
| discord/access | discord | Approve pairings, edit allowlists, set DM/group policy |
| telegram/configure | telegram | Bot token (from BotFather) and access policy |
| telegram/access | telegram | Pairings, allowlists, DM/group policy |
| imessage/configure | imessage | Check channel setup, review access policy |
| imessage/access | imessage | Pairings, allowlists, DM/group policy |

## Consolidated guidance

1. **Two axes, always.** *Connectivity* (does the bridge run?) and *policy*
   (who is allowed in?). Diagnose failures on these axes separately — a
   "channel not responding" is a connectivity bug; "channel ignored me" is a
   policy bug.
2. **Deny by default.** Start with an empty allowlist and add explicitly.
   Group/DM policy should default to closed; opening a group channel to an
   agent with tool access is a real attack surface.
3. **Pairing is an explicit, human step.** Approve pairings deliberately;
   never auto-approve because it is convenient.
4. **Tokens are secrets.** Store bot tokens in config, never in the repo,
   never echoed in logs or chat output.
5. **Configure then verify.** After saving credentials, actually send a
   message through the channel and confirm round-trip before declaring setup
   complete.
6. **Policy changes take effect immediately** — re-state the resulting policy
   to the user after every edit so there is no ambiguity about who is in.
7. **Platform differences are minor but real:** Discord has guild/channel
   granularity, Telegram has group vs supergroup semantics, iMessage is
   contact-based and macOS-bound. Check the platform's own model rather than
   assuming symmetry.

## When to use this skill

"Connect Claude to Discord/Telegram/iMessage", "approve this pairing",
"add this person to the allowlist", "should I allow DMs?", "my Telegram
bridge is dead".

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| discord | configure | unknown |
| discord | access | unknown |
| telegram | configure | unknown |
| telegram | access | unknown |
| imessage | configure | unknown |
| imessage | access | unknown |
