# Synthetic local-paper fixtures

These notes describe engineering inputs. They are not market evidence, not a user strategy, and not selected capital or risk limits.

`paper_config_template.json` is the unrun 100 EUR paper template: taker-only, BTC observation first, strategy and exit unselected, FX uncited, first bound 3 days or 5 simulated trades. It is not a campaign and not readiness.

The simulator tests build configs in code and label every tape `synthetic_engineering_fixture`. A captured public replay is a different evidence class and still does not send an order. Do not read a balance printed from these fixtures as a trading result.
