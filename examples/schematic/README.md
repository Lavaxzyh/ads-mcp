# Synthetic schematic fixtures

`output_match_short.json` is the short template input; `output_match.json` is its expanded equivalent. All electrical values in this publication fixture are explicitly chosen synthetic values, not a contributed user's PA design or a recommended matching solution.

The fixture uses two 50-ohm ports, a 1.0–1.2 GHz linear-SP controller, an ideal 0 V bias source, built-in microstrip elements and ideal capacitors. There is no transistor. The Optim/Goal configuration is disabled. Construction does not launch a simulation.

Replace `target.workspace` with a new absolute directory inside the intended task directory before using `operation=build`. The sample `C:/ads-mcp-example/task/design_wrk` is not an existing installation path. Confirm all dimensions, substrate settings, targets, ports and reference planes for a real design; do not inherit fixture defaults.
