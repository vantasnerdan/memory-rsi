---
description: NaN policy — never fillna, missing means unknown
author: mlops-kelvin
created: 2026-02-10T08:00:00Z
updated: 2026-02-11T14:30:00Z
tags: [data, policy, nan]
related: ["[[shift-one-policy]]"]
confidence: established
category: atlas
status: active
---
# NaN Policy

## Rationale
Why missing values must not be filled with defaults.

NaN represents genuine absence of data. Filling with zeros or means
introduces silent bias that compounds through the pipeline.

## Implementation
How the pipeline enforces NaN propagation.

Every transform checks for NaN and propagates it forward rather
than substituting default values.

## Exceptions
Cases where None is substituted instead of NaN.

String columns use None instead of NaN since NaN is a float concept.
