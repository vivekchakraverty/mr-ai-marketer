# Integration

`app.routers.personas.get_active_personas()` returns the latest completed persona set. `set_active_personas(run_id, personas)` validates evidence references before storing a reviewed set.

The Marketing Plan Generator has an optional **Target persona** selector. The backend resolves a saved completed persona and appends its label, summary and confidence to the plan brief. The prompt explicitly treats it as directional research. No persona is injected unless selected.

Further integration points are Brand Studio's intake, social-post brief creation and campaign planning. Their prompts should consume only a chosen reviewed persona, include confidence and evidence limitations, and never treat user-assumed demographics as measured facts.
