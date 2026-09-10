# Agent Engine inventory — 2026-09-10

Snapshot taken before a teardown. 33 engines; traffic measured since 2026-08-11.

- **delete:** 1
- **keep:** 32
- **always-warm instances:** 14 -> 14

The policy and its reasoning live in [engine-lifecycle.md](engine-lifecycle.md).

| disposition | created | display name | id | warm | reason |
| --- | --- | --- | --- | --- | --- |
| DELETE | 2026-09-09 13:17 | `gepa-c07-ctrl-sonnet5` | `365228625689051136` |  | ephemeral, campaign complete, unreferenced |
| keep | 2026-02-13 21:10 | `simple_adk_agent` | `5742251153106665472` |  | not ours to delete — no ownership label |
| keep | 2026-03-12 19:31 | `novastorm-dssib-synth-retail` | `9067381023687311360` |  | not ours to delete — no ownership label |
| keep | 2026-03-13 08:10 | `novastormy-agent` | `3124705393511497728` |  | not ours to delete — no ownership label |
| keep | 2026-03-18 03:12 | `novastorm-20260318031220` | `3739253228601081856` |  | not ours to delete — no ownership label |
| keep | 2026-04-17 21:23 | `novastorm-20260417212303` | `8659270345003368448` |  | not ours to delete — no ownership label |
| keep | 2026-05-22 10:47 | `pro-gemini-3.1-pro` | `8730635246715797504` |  | not ours to delete — no ownership label |
| keep | 2026-05-22 10:47 | `lite-gemini-3.1-flash-lite` | `4981388556929859584` |  | not ours to delete — no ownership label |
| keep | 2026-05-22 10:52 | `sonnet-claude-4` | `7615994338941599744` |  | not ours to delete — no ownership label |
| keep | 2026-05-22 10:53 | `flash-gemini-3.5-flash` | `6589173623901126656` |  | not ours to delete — no ownership label |
| keep | 2026-05-22 10:59 | `opus-claude-4` | `7807397323104845824` |  | not ours to delete — no ownership label |
| keep | 2026-06-06 20:31 | `flash-lite-gemini-3.1_wrangler-v8` | `3525762705103257600` |  | not ours to delete — no ownership label |
| keep | 2026-06-06 20:37 | `flash-gemini-3.5_wrangler-v8` | `4124741455543533568` |  | not ours to delete — no ownership label |
| keep | 2026-06-06 20:42 | `pro-gemini-3.1_wrangler-v8` | `6633246447988899840` |  | not ours to delete — no ownership label |
| keep | 2026-06-06 20:48 | `gepa-sonnet` | `6943994822277464064` |  | referenced in .env / manifest / experiment |
| keep | 2026-06-06 21:29 | `opus-claude-4_wrangler-v8` | `3543777103612739584` |  | not ours to delete — no ownership label |
| keep | 2026-07-13 14:38 | `trend-trawler-sessions` | `70718938631110656` |  | not ours to delete — no ownership label |
| keep | 2026-07-14 22:07 | `creative-trend-agent-v7` | `5444779931479310336` | 1 | not ours to delete — no ownership label |
| keep | 2026-07-14 22:13 | `trend-scout-agent-v2` | `7273452486424264704` | 1 | not ours to delete — no ownership label |
| keep | 2026-08-12 12:20 | `router_agent_jt1` | `6134089059699523584` | 4 | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-13 09:39 | `coordinator_agent_jt1` | `3639024497392091136` | 4 | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-14 20:26 | `coordinator_agent` | `4380288848559603712` | 4 | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-22 00:15 | `gepa-sonnet` | `2846971505114349568` |  | traffic in window (377 requests) |
| keep | 2026-08-22 00:22 | `gepa-flash` | `2266570103136976896` |  | traffic in window (338 requests) |
| keep | 2026-08-22 00:22 | `gepa-pro` | `4115297750172565504` |  | traffic in window (178 requests) |
| keep | 2026-08-23 00:07 | `lite_agent_jt1` | `4744816535585947648` |  | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-23 00:11 | `flash_agent_jt1` | `7050659544799641600` |  | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-23 00:16 | `pro_agent_jt1` | `1047361241514770432` |  | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-23 00:20 | `sonnet_agent_jt1` | `5659047259942158336` |  | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-23 00:25 | `opus_agent_jt1` | `3508578437872746496` |  | not ours to delete — labelled solution=geap-tour |
| keep | 2026-09-01 04:29 | `novastorm-20260901042920` | `3274140568598347776` |  | not ours to delete — no ownership label |
| keep | 2026-09-08 14:18 | `gepa-c07-sonnet5` | `8490988990860623872` |  | traffic in window (1017 requests) |
| keep | 2026-09-09 13:17 | `gepa-c07-pro` | `7109369067676368896` |  | traffic in window (444 requests) |
