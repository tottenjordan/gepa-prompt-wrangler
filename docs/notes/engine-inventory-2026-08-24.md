# Agent Engine inventory — 2026-08-24

Snapshot taken before a teardown. 80 engines; traffic measured since 2026-07-25.

- **delete:** 42
- **keep:** 38
- **always-warm instances:** 61 -> 33

The policy and its reasoning live in [engine-lifecycle.md](engine-lifecycle.md).

| disposition | created | display name | id | warm | reason |
| --- | --- | --- | --- | --- | --- |
| DELETE | 2026-05-29 00:56 | `wrangler-lite-agent` | `8685308979372359680` |  | legacy list, no traffic, unreferenced |
| DELETE | 2026-05-29 01:02 | `flash-gemini-3.5-flash` | `4703001008869998592` |  | legacy list, no traffic, unreferenced |
| DELETE | 2026-05-29 01:08 | `wrangler-pro-agent` | `6112627692236963840` |  | legacy list, no traffic, unreferenced |
| DELETE | 2026-05-29 01:13 | `sonnet-claude-4` | `1374840884243202048` |  | legacy list, no traffic, unreferenced |
| DELETE | 2026-05-29 01:18 | `opus-claude-4` | `4549878621539401728` |  | legacy list, no traffic, unreferenced |
| DELETE | 2026-06-11 18:58 | `gepa-opus47` | `5353590835118080000` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-12 00:03 | `gepa-opus` | `7338552370881626112` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-17 17:37 | `gepa-opus48` | `8657632072677982208` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-17 22:33 | `gepa-sonnet` | `7729609074462949376` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-18 13:42 | `gepa-opus46` | `3460055890227363840` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-23 20:11 | `gepa-flash` | `3975190281979953152` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-24 02:01 | `gepa-sonnet` | `4152800992284377088` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-24 02:37 | `gepa-opus48` | `6075838033171578880` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-24 15:08 | `gepa-sonnet` | `8225743905287569408` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-24 18:59 | `gepa-pro` | `7836393643752554496` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-24 21:31 | `gepa-flash` | `858066021141970944` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-24 23:18 | `gepa-lite` | `2633610174232788992` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-25 14:40 | `gepa-sonnet` | `2342846523290681344` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-25 18:10 | `gepa-opus48` | `3966394188957745152` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-25 20:51 | `gepa-sonnet` | `7707759579395784704` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-25 23:47 | `gepa-pro` | `8074802949026480128` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-26 02:53 | `gepa-flash` | `6685864676447748096` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-26 05:56 | `gepa-lite` | `5407968282181369856` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-26 09:09 | `gepa-opus48_bare` | `957848900385898496` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-26 12:03 | `gepa-sonnet_bare` | `5954311211976753152` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-26 15:02 | `gepa-pro_bare` | `1946107543617011712` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-26 18:06 | `gepa-flash_bare` | `1690528264763736064` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-06-27 02:30 | `gepa-lite_bare` | `7757466301064282112` |  | labelled ours, no traffic, unreferenced |
| DELETE | 2026-08-23 17:29 | `geap-probe-bare-claude` | `3191356139119837184` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-23 17:47 | `geap-probe-mcp-claude` | `8437205280076333056` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-23 17:53 | `geap-probe-mcp-gemini` | `554498557294411776` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-23 17:58 | `geap-probe-bare-gemini` | `1373309264545710080` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:05 | `geap-probe-lottery-01` | `923793726738792448` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:09 | `geap-probe-lottery-02` | `4728209511960018944` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:14 | `geap-probe-lottery-03` | `8555143295318097920` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:19 | `geap-probe-lottery-04` | `6346690628046290944` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:24 | `geap-probe-lottery-05` | `4040847618832596992` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:28 | `geap-probe-lottery-06` | `5725756829422583808` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:34 | `geap-probe-lottery-07` | `3482401265038655488` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:39 | `geap-probe-lottery-08` | `890016729533513728` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:44 | `geap-probe-lottery-09` | `12377752149688320` | 2 | ephemeral, campaign complete, unreferenced |
| DELETE | 2026-08-24 01:50 | `geap-probe-lottery-10` | `642881699981557760` | 2 | ephemeral, campaign complete, unreferenced |
| keep | 2026-02-13 21:10 | `simple_adk_agent` | `5742251153106665472` |  | not ours to delete — no ownership label |
| keep | 2026-03-12 19:31 | `novastorm-dssib-synth-retail` | `9067381023687311360` |  | not ours to delete — no ownership label |
| keep | 2026-03-13 08:10 | `novastormy-agent` | `3124705393511497728` |  | not ours to delete — no ownership label |
| keep | 2026-03-18 03:12 | `novastorm-20260318031220` | `3739253228601081856` |  | not ours to delete — no ownership label |
| keep | 2026-04-17 21:23 | `novastorm-20260417212303` | `8659270345003368448` |  | not ours to delete — no ownership label |
| keep | 2026-05-13 08:20 | `router_agent` | `4709107696450666496` |  | not ours to delete — no ownership label |
| keep | 2026-05-21 21:55 | `sonnet_agent` | `8467456143491334144` |  | not ours to delete — no ownership label |
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
| keep | 2026-08-20 19:15 | `gepa-agent` | `5638288480409747456` | 2 | traffic in window (397 requests) |
| keep | 2026-08-21 11:30 | `wrangler-sonnet-agent-v4` | `3411962152116813824` | 2 | traffic in window (1618 requests) |
| keep | 2026-08-21 11:36 | `wrangler-lite-agent-v4` | `4365599373212516352` | 2 | referenced and kept warm deliberately |
| keep | 2026-08-21 11:42 | `wrangler-flash-agent-v4` | `7752306292995129344` | 2 | referenced and kept warm deliberately |
| keep | 2026-08-21 11:48 | `wrangler-pro-agent-v4` | `733446273738211328` | 2 | referenced and kept warm deliberately |
| keep | 2026-08-21 11:53 | `wrangler-opus-agent-v4` | `188510718826381312` | 2 | referenced and kept warm deliberately |
| keep | 2026-08-21 12:46 | `sonnet_wrangler-v8` | `3804901219604889600` | 2 | traffic in window (510 requests) |
| keep | 2026-08-22 00:15 | `gepa-sonnet` | `2846971505114349568` |  | traffic in window (377 requests) |
| keep | 2026-08-22 00:22 | `gepa-flash` | `2266570103136976896` |  | traffic in window (338 requests) |
| keep | 2026-08-22 00:22 | `gepa-pro` | `4115297750172565504` |  | traffic in window (178 requests) |
| keep | 2026-08-23 00:07 | `lite_agent_jt1` | `4744816535585947648` | 1 | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-23 00:11 | `flash_agent_jt1` | `7050659544799641600` | 1 | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-23 00:16 | `pro_agent_jt1` | `1047361241514770432` | 1 | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-23 00:20 | `sonnet_agent_jt1` | `5659047259942158336` | 1 | not ours to delete — labelled solution=geap-tour |
| keep | 2026-08-23 00:25 | `opus_agent_jt1` | `3508578437872746496` | 1 | not ours to delete — labelled solution=geap-tour |
