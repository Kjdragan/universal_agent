---
trigger: always_on
---

Variables and secrets used in our project, regardless of whether it is locally on the desktop or on the VPS, should be retrieved from variables saved in the Infisical service that you have programatic access to through a cli.  No variables should be stored in a .env or equivalent file unless It is part of the bootstrapping of that machine in its original environment before it has access to getting infisical stored parameters.
